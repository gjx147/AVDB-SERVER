"""新作品监控服务 —— 检测订阅演员的新作品。

完整流程：
1. 用浏览器池抓演员作品页（/search?q={name}&f=actor）
2. 解析作品列表（番号 + 标题 + 详情链接 + 封面）
3. 与 tasks 表已有番号 + new_releases 表去重
4. 新作品写入 new_releases 表
5. Emby 比对：已在媒体库的标记跳过；不在库的才是真正新作
6. 真正新作 → 发通知（notify new_works）
7. auto_add=True → 创建 task + 触发 scraper 爬详情 + 延迟 push 下载

设计：
- async（挂 APScheduler 或 subscription_monitor 调用）
- 浏览器池抓取 + BeautifulSoup 解析
- 番号比对去重 + Emby 在库比对
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

from bs4 import BeautifulSoup
from sqlalchemy import select

from config import get_settings
from database import SessionLocal
from models import Actor, Download, ListSource, NewRelease, Task

logger = logging.getLogger("avdb.new_works")

# 从标题/链接提取番号
_CODE_RE = re.compile(r"([A-Za-z]{2,6})[-_]?(\d{2,5})")


def _extract_code(text: str) -> str | None:
    m = _CODE_RE.search(text)
    if m:
        return f"{m.group(1).upper()}-{m.group(2)}"
    return None


async def _resolve_actor_url(actor_name: str) -> str | None:
    """搜索 javdb 演员页，返回第一个匹配的 /actors/xxx 完整 URL。

    用于 source_url 为空时自动解析演员详情页 URL。
    """
    from urllib.parse import quote, urljoin
    from services.browser_pool import browser_pool
    from config import get_settings

    search_url = f"{get_settings().JAVDB_URL}/search?f=actor&q={quote(actor_name)}"
    logger.info(f"[新作监控] 搜索演员: {actor_name} -> {search_url}")

    async with browser_pool.acquire() as ctx:
        page = await ctx.new_page()
        try:
            try:
                from playwright_stealth import stealth_async
                await stealth_async(page)
            except Exception:
                pass
            await page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
            await browser_pool._handle_cloudflare(page, search_url)
            await page.wait_for_timeout(2000)

            # 找所有 /actors/xxx 链接，过滤分类页
            links = await page.locator("a[href*='/actors/']").all()
            for link in links:
                try:
                    href = await link.get_attribute("href") or ""
                    # 排除分类路径
                    if any(cat in href for cat in [
                        "/actors/censored", "/actors/uncensored",
                        "/actors/western", "/actors/anime", "/actors/recommend",
                    ]):
                        continue
                    text = (await link.inner_text()).strip()
                    if not text or len(text) < 2:
                        continue
                    # 匹配演员名（包含即可，处理空格/变体）
                    if actor_name in text or text in actor_name:
                        full_url = urljoin(get_settings().JAVDB_URL, href)
                        logger.info(f"[新作监控] 匹配到演员: {text} -> {full_url}")
                        return full_url
                except Exception:
                    continue
            # 没精确匹配，取第一个非分类的 /actors/ 链接
            for link in links:
                try:
                    href = await link.get_attribute("href") or ""
                    if any(cat in href for cat in [
                        "/actors/censored", "/actors/uncensored",
                        "/actors/western", "/actors/anime", "/actors/recommend",
                    ]):
                        continue
                    text = (await link.inner_text()).strip()
                    if text and len(text) >= 2:
                        full_url = urljoin(get_settings().JAVDB_URL, href)
                        logger.info(f"[新作监控] 取首个演员链接: {text} -> {full_url}")
                        return full_url
                except Exception:
                    continue
            return None
        finally:
            await page.close()


async def _fetch_actor_works(actor_url: str) -> list[dict]:
    """抓演员作品页，解析作品列表。返回 [{code, title, url, cover}]。

    用 browser_pool.acquire() 直接操作 page（不用 fetch_html），因为 javdb
    搜索结果是 JS 动态加载的，需要等 /v/ 链接出现后再解析。
    """
    from services.browser_pool import browser_pool

    works = []
    async with browser_pool.acquire() as ctx:
        page = await ctx.new_page()
        try:
            # stealth 反检测
            try:
                from playwright_stealth import stealth_async
                await stealth_async(page)
            except Exception:
                pass

            await page.goto(actor_url, timeout=30000, wait_until="domcontentloaded")

            # Cloudflare 验证处理
            await browser_pool._handle_cloudflare(page, actor_url)

            # 等待作品列表加载（最多 15 秒等 /v/ 链接出现）
            try:
                await page.wait_for_selector("a[href*='/v/']", timeout=15000)
            except Exception:
                logger.warning("[新作监控] 等待 /v/ 链接超时，可能页面无作品或未加载完")

            # 额外等待确保 JS 渲染完
            await page.wait_for_timeout(2000)

            # 用 page.locator 在浏览器内解析（比 BeautifulSoup 离线解析更可靠）
            items = await page.locator(
                ".movie-list .item, .video-list .item, .grid-item, .movie-list > div"
            ).all()

            # 兜底：如果上面选择器没匹配到，直接找所有含 /v/ 的链接
            if not items:
                logger.info("[新作监控] 常规选择器无匹配，用 /v/ 链接兜底解析")
                links = await page.locator("a[href*='/v/']").all()
                for link in links:
                    try:
                        href = await link.get_attribute("href") or ""
                        text = (await link.inner_text()).strip()
                        code = _extract_code(href) or _extract_code(text)
                        if code:
                            works.append({"code": code, "title": text[:100], "url": href, "cover": None})
                    except Exception:
                        continue
                return works

            for item in items:
                try:
                    link = item.locator("a[href*='/v/']").first
                    if await link.count() == 0:
                        continue
                    href = await link.get_attribute("href") or ""
                    # 标题
                    title = ""
                    try:
                        title_el = item.locator(".video-title strong, .title, .video-title").first
                        if await title_el.count() > 0:
                            title = (await title_el.inner_text()).strip()
                    except Exception:
                        pass
                    code = _extract_code(href) or _extract_code(title)
                    # 封面
                    cover = None
                    try:
                        img = item.locator("img").first
                        if await img.count() > 0:
                            cover = await img.get_attribute("src") or await img.get_attribute("data-src")
                    except Exception:
                        pass
                    if code:
                        works.append({"code": code, "title": title, "url": href, "cover": cover})
                except Exception:
                    continue
        finally:
            await page.close()
    return works


async def check_actor_new_works(actor_id: int, subscription_id: int | None = None,
                                 auto_add: bool = False) -> dict:
    """检测某演员的新作品。返回摘要。

    流程：抓 javdb → 去重 → 写 new_releases → Emby 比对 → 通知 → 可选自动下载。
    """
    db = SessionLocal()
    try:
        actor = db.get(Actor, actor_id)
        if not actor:
            logger.warning(f"[新作监控] 演员不存在 actor_id={actor_id}")
            return {"error": "演员不存在", "actor_id": actor_id}
        if not actor.name:
            logger.warning(f"[新作监控] 演员无名字 actor_id={actor_id}")
            return {"error": "演员无名字", "actor_id": actor_id}

        logger.info(f"[新作监控] 开始检查演员 {actor.name} (id={actor_id}, auto_add={auto_add})")
        settings = get_settings()
        # 优先用演员详情页 URL（/actors/xxx，直接列出所有作品）
        actor_url = actor.source_url or ""
        if not actor_url:
            # source_url 为空时尝试从 note 解析（老数据格式 "source_url: xxx"）
            note = actor.note or ""
            if note.startswith("source_url:"):
                actor_url = note.split(":", 1)[1].strip()
        if not actor_url:
            # 仍然没有：搜索演员页，解析第一个 /actors/xxx 链接并存入 source_url
            logger.info(f"[新作监控] {actor.name} 无 source_url，搜索演员页解析 URL")
            actor_url = await _resolve_actor_url(actor.name)
            if actor_url:
                actor.source_url = actor_url
                db.commit()
                logger.info(f"[新作监控] {actor.name} 已保存 source_url: {actor_url}")
            else:
                actor_url = f"{settings.JAVDB_URL}/search?q={actor.name}&f=actor"
                logger.warning(f"[新作监控] {actor.name} 搜索未找到演员页，回退搜索页: {actor_url}")
        else:
            logger.info(f"[新作监控] {actor.name} 用演员详情页: {actor_url}")
        try:
            works = await _fetch_actor_works(actor_url)
        except Exception as e:
            logger.warning(f"[新作监控] 抓取演员 {actor.name} 作品失败: {e}")
            return {"type": "actor", "actor_id": actor_id, "error": f"抓取失败: {e}"}
        logger.info(f"[新作监控] {actor.name}: 抓到 {len(works)} 部作品")

        # 去重：已有 task 的 + 已在 new_releases 的
        existing_codes: set[str] = set()
        for r in db.execute(select(Task.video_code).where(Task.video_code.isnot(None))).all():
            existing_codes.add(r[0])
        for r in db.execute(
            select(NewRelease.video_code).where(NewRelease.actor_id == actor_id)
        ).all():
            existing_codes.add(r[0])

        new_works = [w for w in works if w["code"] not in existing_codes]
        added_releases: list[NewRelease] = []
        for w in new_works:
            nr = NewRelease(
                actor_id=actor_id,
                video_code=w["code"],
                title=w["title"],
                detail_url=w["url"],
                cover_url=w["cover"],
            )
            db.add(nr)
            added_releases.append(nr)
        db.flush()  # 拿到 nr.id

        # ── Emby 比对：已在媒体库的标记跳过 ──
        from services.media_server import check_in_library

        truly_new: list[NewRelease] = []  # Emby 不在库的真正新作
        in_library_count = 0
        for nr in added_releases:
            try:
                in_lib = await check_in_library(nr.video_code)
            except Exception as e:
                logger.warning(f"Emby 查询 {nr.video_code} 失败（视为不在库）: {e}")
                in_lib = False
            if in_lib:
                nr.added_to_library = True
                nr.is_read = True  # 已在库，标记已读
                in_library_count += 1
            else:
                truly_new.append(nr)
        db.commit()

        # ── 发通知（有真正新作才发）──
        if truly_new:
            from services.notifier import notify
            codes = [nr.video_code for nr in truly_new]
            try:
                await notify(
                    "new_works",
                    f"{actor.name} 新作品",
                    f"新增 {len(truly_new)} 部: {', '.join(codes[:5])}"
                    + ("…" if len(codes) > 5 else ""),
                )
            except Exception as e:
                logger.warning(f"通知发送失败（不影响主流程）: {e}")

        # ── auto_add：自动创建 task + 爬详情 + push 下载 ──
        pushed = 0
        if auto_add and truly_new:
            for nr in truly_new:
                try:
                    task_id = await _create_task_and_extract(nr)
                    if task_id:
                        nr.task_id = task_id
                        nr.added_to_library = True
                        pushed += 1
                except Exception as e:
                    logger.warning(f"自动下载 {nr.video_code} 失败: {e}")
            db.commit()

        total_unread = len(db.execute(
            select(NewRelease).where(
                NewRelease.actor_id == actor_id,
                NewRelease.is_read == False,  # noqa: E712
            )
        ).scalars().all())

        return {
            "type": "actor",
            "actor_id": actor_id,
            "actor_name": actor.name,
            "scanned": len(works),
            "new_count": len(added_releases),
            "in_library": in_library_count,
            "truly_new": len(truly_new),
            "pushed": pushed,
            "total_unread": total_unread,
        }
    finally:
        db.close()


async def _create_task_and_extract(nr: NewRelease) -> int | None:
    """为新作品创建 task，触发 scraper 爬详情拿磁力，延迟 push 下载。返回 task_id。

    nr 对象需已 flush（有 id）。本函数不改 nr 状态（由调用方提交）。
    """
    db = SessionLocal()
    try:
        # 1. 创建 task（pending，关联 RANKING list_source）
        src = db.execute(
            select(ListSource).where(ListSource.list_code == "RANKING")
        ).scalar_one_or_none()
        if not src:
            src = ListSource(list_code="RANKING", list_path="/rankings")
            db.add(src)
            db.flush()
        # task.url：有 detail_url 用真实 URL，否则用 pending:// 番号
        task_url = nr.detail_url or f"pending://{nr.video_code}"
        task = Task(
            list_source_id=src.id,
            url=task_url,
            video_code=nr.video_code,
            status="pending",
        )
        db.add(task)
        db.flush()
        task_id = task.id
        db.commit()
        logger.info(f"[新作监控] 创建 task {task_id} for {nr.video_code}")

        # 2. 触发 scraper extract-single（subprocess，非阻塞 fire-and-forget）
        from services.scraper_lock import is_running
        if is_running():
            logger.info(f"[新作监控] scraper 忙，task {task_id} 排队等 auto_retry")
        else:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "python", "/app/magnet_scraper/scraper.py",
                    "extract-single", "--url", task_url,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                logger.info(f"[新作监控] 触发 scraper extract-single task {task_id} (pid={proc.pid})")
            except Exception as e:
                logger.warning(f"[新作监控] 触发 scraper 失败（task {task_id} 保留 pending）: {e}")

        # 3. 延迟检查 task 是否拿到磁力，有则自动 push
        asyncio.create_task(_delayed_push_if_ready(task_id, nr.video_code, delay=180))
        return task_id
    finally:
        db.close()


async def _delayed_push_if_ready(task_id: int, video_code: str, delay: int = 180):
    """延迟 N 秒后检查 task 是否爬完拿到磁力，有则自动 push 下载。

    scraper extract-single 是异步 subprocess，需要等它爬完。180 秒后检查，
    如果还没磁力就跳过（auto_retry 会后续重试）。
    """
    await asyncio.sleep(delay)
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if not task or not task.best_magnet:
            logger.info(
                f"[新作监控] task {task_id} {video_code} 暂无磁力（scraper 可能未完成），跳过自动 push"
            )
            return

        # 读下载器配置
        from routers.downloaders import _get_setting, _push_clouddrive, _push_qbittorrent, _extract_hash
        config_keys = [
            "qb_url", "qb_username", "qb_password", "qbittorrent_save_path",
            "clouddrive_url", "clouddrive_token", "clouddrive_username",
            "clouddrive_password", "clouddrive_save_path",
        ]
        config = {k: _get_setting(db, k) for k in config_keys}
        downloader = _get_setting(db, "default_downloader") or "qbittorrent"

        logger.info(f"[新作监控] 自动推送 {video_code} 到 {downloader}")
        if downloader == "clouddrive":
            result = await _push_clouddrive(task.best_magnet, config)
        else:
            result = await _push_qbittorrent(task.best_magnet, config)

        if result.get("ok"):
            # 记录 download
            dl = Download(
                task_id=task_id,
                video_code=video_code,
                magnet=task.best_magnet,
                info_hash=_extract_hash(task.best_magnet),
                downloader=downloader,
                status="pushed",
            )
            db.add(dl)
            db.commit()
            logger.info(f"[新作监控] 自动推送 {video_code} 成功")

            # 触发 CD2 迁移（push_magnet 内置钩子是 HTTP 路径才触发，这里手动调）
            try:
                from services.cd2_organize import schedule_organize
                schedule_organize(task_id, video_code)
            except Exception as e:
                logger.warning(f"[新作监控] CD2 迁移钩子失败（不影响推送）: {e}")
        else:
            logger.warning(f"[新作监控] 自动推送 {video_code} 失败: {result.get('message')}")
    except Exception as e:
        logger.error(f"[新作监控] _delayed_push_if_ready 异常 ({video_code}): {e}")
    finally:
        db.close()


async def run_check_all(auto_add: bool = False) -> dict:
    """对所有关注/订阅的演员执行新作品检测。"""
    logger.info(f"[新作监控] 开始批量巡检 (auto_add={auto_add})")
    db = SessionLocal()
    try:
        from models import Subscription

        followed = db.execute(
            select(Actor).where(Actor.is_followed == True)  # noqa: E712
        ).scalars().all()
        sub_actors = db.execute(
            select(Actor).where(Actor.id.in_(
                select(Subscription.actor_id).where(
                    Subscription.sub_type == "actor",
                    Subscription.enabled == True,  # noqa: E712
                )
            ))
        ).scalars().all()
        actor_ids = {a.id for a in followed} | {a.id for a in sub_actors}
        logger.info(
            f"[新作监控] 关注演员 {len(followed)} 个，订阅演员 {len(sub_actors)} 个，"
            f"合并去重后 {len(actor_ids)} 个待检查"
        )
    finally:
        db.close()

    results = []
    total_new = 0
    total_pushed = 0
    for aid in actor_ids:
        r = await check_actor_new_works(aid, auto_add=auto_add)
        results.append(r)
        total_new += r.get("truly_new", r.get("new_count", 0))
        total_pushed += r.get("pushed", 0)
    return {
        "ok": True,
        "checked_actors": len(actor_ids),
        "total_new": total_new,
        "total_pushed": total_pushed,
        "results": results,
    }


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
    src = db.execute(
        select(ListSource).where(ListSource.list_code == "RANKING")
    ).scalar_one_or_none()
    if not src:
        src = ListSource(list_code="RANKING", list_path="/rankings")
        db.add(src)
        db.flush()
    t = Task(list_source_id=src.id, url=nr.detail_url or f"/v/{nr.video_code}",
             video_code=nr.video_code)
    db.add(t)
    db.flush()
    nr.added_to_library = True
    nr.task_id = t.id
    nr.is_read = True
    return t.id
