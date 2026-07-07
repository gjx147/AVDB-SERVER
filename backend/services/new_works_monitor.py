"""新作品监控服务 —— 检测订阅演员的新作品。

流程（参考 JavdBviewed newWorks）：
1. 用浏览器池抓演员作品页（/actors/{id}）
2. 解析作品列表（番号 + 标题 + 详情链接 + 封面）
3. 与 tasks 表已有番号 + new_releases 表去重
4. 新作品写入 new_releases 表
5. 可选 auto_add：入库为 pending task
6. 通知（通过 notifier，Phase 3 第6步）

设计：
- async（挂 APScheduler 或 subscription_monitor 调用）
- 浏览器池抓取 + BeautifulSoup 解析
- 番号比对去重
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup
from sqlalchemy import select

from config import get_settings
from database import SessionLocal
from models import Actor, ListSource, NewRelease, Task

logger = logging.getLogger("avdb.new_works")

# 从标题/链接提取番号
_CODE_RE = re.compile(r"([A-Za-z]{2,6})[-_]?(\d{2,5})")


def _extract_code(text: str) -> str | None:
    m = _CODE_RE.search(text)
    if m:
        return f"{m.group(1).upper()}-{m.group(2)}"
    return None


async def _fetch_actor_works(actor_url: str) -> list[dict]:
    """抓演员作品页，解析作品列表。返回 [{code, title, url, cover}]。"""
    from services.browser_pool import browser_pool

    html = await browser_pool.fetch_html(actor_url, timeout=30000, wait_until="domcontentloaded")
    soup = BeautifulSoup(html, "html.parser")
    works = []
    # JavDB 作品卡片选择器（movie-list / video-list）
    for item in soup.select(".movie-list .item, .video-list .item, .grid-item"):
        link = item.select_one("a[href^='/v/']")
        if not link:
            continue
        href = link.get("href", "")
        title_el = item.select_one(".video-title strong, .title")
        title = title_el.get_text(strip=True) if title_el else ""
        code = _extract_code(href) or _extract_code(title)
        cover_el = item.select_one("img")
        cover = cover_el.get("src") or cover_el.get("data-src") if cover_el else None
        if code:
            works.append({"code": code, "title": title, "url": href, "cover": cover})
    return works


async def check_actor_new_works(actor_id: int, subscription_id: int | None = None) -> dict:
    """检测某演员的新作品。返回摘要。"""
    db = SessionLocal()
    try:
        actor = db.get(Actor, actor_id)
        if not actor:
            return {"error": "演员不存在", "actor_id": actor_id}
        if not actor.name:
            return {"error": "演员无名字", "actor_id": actor_id}

        settings = get_settings()
        actor_url = f"{settings.JAVDB_URL}/search?q={actor.name}&f=actor"
        try:
            works = await _fetch_actor_works(actor_url)
        except Exception as e:
            logger.warning(f"抓取演员 {actor.name} 作品失败: {e}")
            return {"type": "actor", "actor_id": actor_id, "error": f"抓取失败: {e}"}

        # 去重：已有 task 的 + 已在 new_releases 的
        existing_codes = set()
        for r in db.execute(select(Task.video_code).where(Task.video_code.isnot(None))).all():
            existing_codes.add(r[0])
        for r in db.execute(select(NewRelease.video_code).where(NewRelease.actor_id == actor_id)).all():
            existing_codes.add(r[0])

        new_works = [w for w in works if w["code"] not in existing_codes]
        added = 0
        for w in new_works:
            nr = NewRelease(
                actor_id=actor_id,
                video_code=w["code"],
                title=w["title"],
                detail_url=w["url"],
                cover_url=w["cover"],
            )
            db.add(nr)
            added += 1
        db.commit()

        return {
            "type": "actor",
            "actor_id": actor_id,
            "actor_name": actor.name,
            "scanned": len(works),
            "new_count": added,
            "total_unread": db.execute(
                select(NewRelease).where(NewRelease.actor_id == actor_id, NewRelease.is_read == False)  # noqa: E712
            ).scalars().all().__len__(),
        }
    finally:
        db.close()


async def run_check_all() -> dict:
    """对所有关注/订阅的演员执行新作品检测。"""
    db = SessionLocal()
    try:
        # 关注的演员 或 有 actor 订阅的
        from models import Subscription

        followed = db.execute(select(Actor).where(Actor.is_followed == True)).scalars().all()  # noqa: E712
        sub_actors = db.execute(
            select(Actor).where(Actor.id.in_(
                select(Subscription.actor_id).where(Subscription.sub_type == "actor", Subscription.enabled == True)  # noqa: E712
            ))
        ).scalars().all()
        actor_ids = {a.id for a in followed} | {a.id for a in sub_actors}
    finally:
        db.close()

    results = []
    total_new = 0
    for aid in actor_ids:
        r = await check_actor_new_works(aid)
        results.append(r)
        total_new += r.get("new_count", 0)
    return {"ok": True, "checked_actors": len(actor_ids), "total_new": total_new, "results": results}


def mark_read(new_release_id: int, db) -> bool:
    """标记新作品为已读。"""
    nr = db.get(NewRelease, new_release_id)
    if nr:
        nr.is_read = True
        return True
    return False


def add_to_library(new_release_id: int, db) -> int | None:
    """把新作品入库为 pending task。返回 task_id。"""
    nr = db.get(NewRelease, new_release_id)
    if not nr or nr.added_to_library:
        return nr.task_id if nr else None
    # 默认 list_source
    src = db.execute(select(ListSource).where(ListSource.list_code == "RANKING")).scalar_one_or_none()
    if not src:
        src = ListSource(list_code="RANKING", list_path="/rankings")
        db.add(src)
        db.flush()
    t = Task(list_source_id=src.id, url=nr.detail_url or f"/v/{nr.video_code}", video_code=nr.video_code)
    db.add(t)
    db.flush()
    nr.added_to_library = True
    nr.task_id = t.id
    nr.is_read = True
    return t.id
