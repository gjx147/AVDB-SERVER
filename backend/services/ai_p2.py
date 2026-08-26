"""P2 批 AI 服务——订阅推荐 / 相似演员 / 季度报告 / 分享摘要 / 异常检测 / 标签归一化。

设计：静态计算优先（不依赖 AI），AI 只做文案与判定增强；全部走 LLMCache。
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import select, func

logger = logging.getLogger("avdb.ai_p2")


async def _ai_text(prompt: str, cache_key: str, task_type: str) -> str | None:
    """LLM 文案（缓存）。失败返回 None。"""
    from services.ai_service import _get_cached, _hash_prompt, _save_cache, chat
    key = _hash_prompt(cache_key)
    cached = _get_cached(key)
    if cached:
        return cached
    try:
        raw = await chat(
            [{"role": "system", "content": "你是 AVDB 影片库智能助手，用简洁自然的中文输出，不要 markdown 标题。"},
             {"role": "user", "content": prompt}],
            task_type=task_type,
        )
        text = str(raw or "").strip()
        if text:
            _save_cache(key, task_type, "", cache_key, text)
        return text or None
    except Exception as e:
        logger.warning("AI 生成失败(%s): %s", task_type, e)
        return None


# ---------- S6 AI 订阅推荐 ----------
async def subscription_suggestions(limit: int = 5) -> dict:
    """推荐值得订阅的演员：作品多/评分高、且未被订阅。"""
    from models import Actor, actor_movies, Subscription, Task
    from database import SessionLocal
    db = SessionLocal()
    try:
        sub_actor_ids = set(db.execute(
            select(Subscription.actor_id).where(Subscription.actor_id.isnot(None))
        ).scalars().all())
        rows = db.execute(
            select(Actor.id, Actor.name, func.count(actor_movies.c.task_id).label("works"),
                   func.avg(Task.rating).label("avg_r"))
            .join(actor_movies, actor_movies.c.actor_id == Actor.id)
            .join(Task, Task.id == actor_movies.c.task_id)
            .where(Actor.id.notin_(sub_actor_ids) if sub_actor_ids else Actor.id.isnot(None))
            .group_by(Actor.id, Actor.name)
            .having(func.count(actor_movies.c.task_id) >= 2)
            .order_by(func.avg(Task.rating).desc())
            .limit(limit + 3)
        ).all()
    finally:
        db.close()
    if not rows:
        return {"ok": True, "items": [], "message": "暂无候选（需要至少 2 部作品的演员）"}
    items = [{"actor_id": r.id, "name": r.name, "works": r.works, "avg_rating": round(r.avg_r or 0, 1)} for r in rows[:limit]]
    names = "、".join(f"{i['name']}({i['works']}部/{i['avg_rating']}分)" for i in items)
    ai = await _ai_text(
        f"以下演员作品多且评分不错但尚未订阅：{names}。用 2-3 句话说明订阅她们的理由（按推荐度排序）。",
        f"sub_suggest:{names}", "sub_suggest")
    return {"ok": True, "items": items, "reason": ai, "engine": "ai" if ai else "rules"}


# ---------- S7 相似演员 ----------
async def similar_actors(actor_id: int, limit: int = 5) -> dict:
    """相似演员：共演网络（同作品次数）+ 标签重合度。"""
    from models import Actor, Task, actor_movies
    from database import SessionLocal
    db = SessionLocal()
    try:
        actor = db.get(Actor, actor_id)
        if not actor:
            return {"ok": False, "message": "演员不存在"}
        my_task_ids = set(db.execute(
            select(actor_movies.c.task_id).where(actor_movies.c.actor_id == actor_id)
        ).scalars().all())
        if not my_task_ids:
            return {"ok": True, "actor_id": actor_id, "name": actor.name, "items": [], "message": "该演员暂无作品关联"}
        my_tags = Counter()
        for tid in my_task_ids:
            t = db.get(Task, tid)
            if t:
                for g in (t.tags or "").split(","):
                    if g.strip():
                        my_tags[g.strip()] += 1
        # 共演者（同作品的其他演员）
        rows = db.execute(
            select(actor_movies.c.actor_id, actor_movies.c.task_id)
            .where(actor_movies.c.task_id.in_(my_task_ids), actor_movies.c.actor_id != actor_id)
        ).all()
        co_count: Counter = Counter()
        for aid, _tid in rows:
            co_count[aid] += 1
        # 标签相似度（库内同标签作品数）
        candidates = []
        for aid, cnt in co_count.most_common(20):
            other = db.get(Actor, aid)
            if not other:
                continue
            other_task_ids = set(db.execute(
                select(actor_movies.c.task_id).where(actor_movies.c.actor_id == aid)
            ).scalars().all())
            other_tags = Counter()
            for tid in list(other_task_ids)[:60]:
                t = db.get(Task, tid)
                if t:
                    for g in (t.tags or "").split(","):
                        if g.strip():
                            other_tags[g.strip()] += 1
            overlap = sum((my_tags & other_tags).values())
            candidates.append({"actor_id": aid, "name": other.name, "co_works": cnt,
                               "tag_overlap": overlap, "score": cnt * 2 + overlap})
        candidates.sort(key=lambda x: x["score"], reverse=True)
        items = candidates[:limit]
    finally:
        db.close()
    if not items:
        return {"ok": True, "actor_id": actor_id, "name": actor.name, "items": [], "message": "未找到相似演员"}
    names = "、".join(f"{i['name']}(共演{i['co_works']}部)" for i in items)
    ai = await _ai_text(
        f"演员 {actor.name} 的相似演员：{names}。用 2 句话说明风格相近点。",
        f"similar:{actor_id}:{names}", "similar")
    return {"ok": True, "actor_id": actor_id, "name": actor.name, "items": items,
            "reason": ai, "engine": "ai" if ai else "rules"}


# ---------- A6 AI 季度观看报告 ----------
async def quarterly_report(year: int | None = None, quarter: int | None = None) -> dict:
    """季度 vs 上季度：入库/下载/收藏 + Top 演员标签变化 → AI 对比。"""
    from models import Task, Download, task_collections
    from database import SessionLocal
    now = datetime.utcnow()
    y = year or now.year
    q = quarter or ((now.month - 1) // 3 + 1)
    q = min(max(int(q), 1), 4)  # E9: 越界钳制

    def q_range(yy: int, qq: int):
        start = datetime(yy, (qq - 1) * 3 + 1, 1)
        end = datetime(yy + (1 if qq == 4 else 0), (qq % 4) * 3 + 1, 1) if qq < 4 else datetime(yy + 1, 1, 1)
        return start, end

    def collect(yy: int, qq: int) -> dict:
        start, end = q_range(yy, qq)
        db = SessionLocal()
        try:
            ts = db.execute(select(Task).where(Task.created_at >= start, Task.created_at < end)).scalars().all()
            dl = db.execute(select(Download.id).where(
                Download.completed_at >= start, Download.completed_at < end, Download.status == "completed")).all()
            fav = db.execute(select(task_collections.c.task_id).where(
                task_collections.c.created_at >= start, task_collections.c.created_at < end)).all()
            ac, tc = Counter(), Counter()
            for t in ts:
                for a in (t.actors or "").split(","):
                    if a.strip():
                        ac[a.strip()] += 1
                for g in (t.tags or "").split(","):
                    if g.strip():
                        tc[g.strip()] += 1
            return {"added": len(ts), "downloads": len(dl), "favorites": len(fav),
                    "top_actors": [a for a, _ in ac.most_common(5)],
                    "top_tags": [g for g, _ in tc.most_common(5)]}
        finally:
            db.close()

    cur = collect(y, q)
    py, pq = (y - 1, 4) if q == 1 else (y, q - 1)
    prev = collect(py, pq)

    data = {"year": y, "quarter": q, "current": cur, "previous": prev}
    ai = await _ai_text(
        f"本季度({y}Q{q})：入库 {cur['added']} 部、下载 {cur['downloads']}、收藏 {cur['favorites']}；"
        f"上季度：入库 {prev['added']}、下载 {prev['downloads']}、收藏 {prev['favorites']}。\n"
        f"本季 Top 演员：{'、'.join(cur['top_actors']) or '无'}；Top 标签：{'、'.join(cur['top_tags']) or '无'}。\n"
        "用 3-4 句话点评本季观看口味变化与亮点（80 字内）。",
        f"quarterly:{y}Q{q}:{cur['added']}:{cur['downloads']}", "quarterly")
    data["summary"] = ai
    data["engine"] = "ai" if ai else "rules"
    return {"ok": True, **data}


# ---------- A8 分享页 AI 摘要 ----------
async def share_summary(token: str) -> dict:
    """收藏夹分享的一句话介绍（名称 + 作品数 + 标签分布）。"""
    from models import ShareToken, Collection, Task, task_collections
    from database import SessionLocal
    db = SessionLocal()
    try:
        st = db.execute(select(ShareToken).where(ShareToken.token == token)).scalar_one_or_none()
        if not st:
            return {"ok": False, "message": "分享不存在"}
        if st.kind != "collection":
            return {"ok": True, "kind": st.kind, "summary": None, "message": "非收藏夹分享，无摘要"}
        col = db.get(Collection, st.ref_id)
        if not col:
            return {"ok": False, "message": "收藏夹不存在"}
        task_ids = [r[0] for r in db.execute(
            select(task_collections.c.task_id).where(task_collections.c.collection_id == col.id)).all()]
        tag_c: Counter = Counter()
        for tid in task_ids:
            t = db.get(Task, tid)
            if t:
                for g in (t.tags or "").split(","):
                    if g.strip():
                        tag_c[g.strip()] += 1
        top_tags = [g for g, _ in tag_c.most_common(5)]
        name, count = col.name, len(task_ids)
    finally:
        db.close()
    prompt = f"收藏夹「{name}」共 {count} 部作品，主要标签：{'、'.join(top_tags) or '无'}。用一句话介绍这个收藏夹（30 字内）。"
    ai = await _ai_text(prompt, f"share_sum:{token}:{name}:{count}", "share_sum")
    summary = ai or f"「{name}」共 {count} 部作品" + (f"，主打{'、'.join(top_tags[:3])}" if top_tags else "")
    return {"ok": True, "kind": "collection", "name": name, "count": count,
            "top_tags": top_tags, "summary": summary, "engine": "ai" if ai else "rules"}


# ---------- A9 元数据异常检测 ----------
_CODE_RE = re.compile(r"^[A-Z]{2,5}-\d{2,5}[A-Z0-9]?$")


def metadata_audit(limit: int = 30) -> dict:
    """静态规则扫描：评分越界 / 番号格式异常 / 标题乱码 / 有磁力缺标题。"""
    from models import Task
    from database import SessionLocal
    problems: list[dict] = []
    db = SessionLocal()
    try:
        rows = db.execute(
            select(Task).where(Task.video_code.isnot(None)).order_by(Task.id.desc()).limit(2000)
        ).scalars().all()
        for t in rows:
            if len(problems) >= limit:
                break
            if t.rating is not None and (t.rating > 10 or t.rating < 0):
                problems.append({"level": "error", "type": "rating", "task_id": t.id,
                                 "video_code": t.video_code, "detail": f"评分异常 {t.rating}"})
            elif t.video_code and not _CODE_RE.match(t.video_code.strip()):
                problems.append({"level": "warning", "type": "code_format", "task_id": t.id,
                                 "video_code": t.video_code, "detail": f"番号格式异常 {t.video_code}"})
            elif t.title and ("\ufffd" in t.title or "?" in t.title and t.title.count("?") > 3):
                problems.append({"level": "warning", "type": "title_garbled", "task_id": t.id,
                                 "video_code": t.video_code, "detail": "标题疑似乱码"})
            elif t.best_magnet and not t.title:
                problems.append({"level": "info", "type": "missing_title", "task_id": t.id,
                                 "video_code": t.video_code, "detail": "有磁力但缺标题"})
    finally:
        db.close()
    return {"ok": True, "total_scanned": min(len(rows), 2000), "problems": problems,
            "error_count": sum(1 for p in problems if p["level"] == "error")}


# ---------- A10 同义标签归一化 ----------
async def tag_normalize_preview(min_count: int = 2, limit: int = 40) -> dict:
    """统计高频标签 → LLM 分组同义 → 返回合并建议（预演，不执行）。"""
    from models import Task
    from database import SessionLocal
    db = SessionLocal()
    try:
        tag_c: Counter = Counter()
        for (tags,) in db.execute(select(Task.tags).where(Task.tags.isnot(None))).all():
            for g in (tags or "").split(","):
                g = g.strip()
                if g:
                    tag_c[g] += 1
    finally:
        db.close()
    common = [g for g, c in tag_c.most_common(60) if c >= min_count][:limit]
    if len(common) < 4:
        return {"ok": True, "groups": [], "message": "标签数量不足，无法归一化"}
    prompt = (
        f"以下是影片库高频标签：{'、'.join(common)}。\n"
        "找出同义/近义标签（如繁简、大小写、别称），输出 JSON 数组：[{\"canonical\": \"标准名\", \"aliases\": [\"同义标签1\", \"同义标签2\"]}]。\n"
        "只输出 JSON，canonical 必须是列表中出现过的标签。"
    )
    try:
        from services.ai_service import chat
        raw = await chat(
            [{"role": "system", "content": "你是标签整理助手，只输出 JSON。"},
             {"role": "user", "content": prompt}],
            task_type="tag_norm",
        )
        m = re.search(r"\[[\s\S]*\]", raw or "")
        groups = json.loads(m.group(0)) if m else []
        groups = [g for g in groups if isinstance(g, dict) and g.get("canonical") and len(g.get("aliases") or []) > 0]
    except Exception:
        # 降级：繁简/大小写规则
        groups = []
        seen = set()
        for g in common:
            key = g.lower()
            if key in seen:
                continue
            aliases = [x for x in common if x != g and x.lower() == key]
            if aliases:
                groups.append({"canonical": g, "aliases": aliases})
                seen.add(key)
    return {"ok": True, "groups": groups, "total_tags": len(tag_c)}


def tag_normalize_apply(groups: list[dict]) -> dict:
    """执行合并：把 aliases 替换为 canonical（逐作品更新 tasks.tags）。"""
    from models import Task
    from database import SessionLocal
    mapping: dict[str, str] = {}
    for g in groups:
        for a in (g.get("aliases") or []):
            mapping[str(a).strip()] = str(g["canonical"]).strip()
    if not mapping:
        return {"ok": False, "message": "无合并映射"}
    db = SessionLocal()
    updated = 0
    try:
        rows = db.execute(select(Task).where(Task.tags.isnot(None))).scalars().all()
        for t in rows:
            parts = [p.strip() for p in (t.tags or "").split(",") if p.strip()]
            changed = False
            new_parts = []
            for p in parts:
                if p in mapping:
                    new_parts.append(mapping[p])
                    changed = True
                else:
                    new_parts.append(p)
            if changed:
                # 去重保留顺序
                seen, out = set(), []
                for p in new_parts:
                    if p not in seen:
                        seen.add(p)
                        out.append(p)
                t.tags = ",".join(out)
                updated += 1
        db.commit()
    finally:
        db.close()
    return {"ok": True, "updated_tasks": updated, "mapping": mapping}
