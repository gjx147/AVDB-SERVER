"""数据洞察路由 —— 聚合统计 + 月报。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from deps import CurrentUser, DbSession
from services.report_generator import aggregate, generate_report, get_report

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("/stats")
def insights_stats(db: DbSession, _user: CurrentUser, month: str | None = Query(None, description="YYYY-MM，空则全部")):
    """实时聚合统计（不入库）。"""
    return aggregate(db, month=month)


@router.post("/reports/{month}")
def create_report(month: str, db: DbSession, _user: CurrentUser):
    """生成/刷新某月月报（存入 insight_reports 表）。"""
    if not month or len(month) != 7:
        raise HTTPException(status_code=400, detail="month 格式应为 YYYY-MM")
    return generate_report(db, month=month)


@router.get("/reports/{month}")
def read_report(month: str, db: DbSession, _user: CurrentUser):
    """读取已存的月报。"""
    report = get_report(db, month)
    if not report:
        raise HTTPException(status_code=404, detail="该月报告未生成")
    return report



@router.get("/activity-heatmap")
def activity_heatmap(db: DbSession, _user: CurrentUser, days: int = Query(180, le=365)):
    """活动热力（F5）：收藏 + 下载行为按天聚合（GitHub 风格热力图数据源）。"""
    from datetime import datetime, timedelta
    from models import Download, task_collections

    since = datetime.utcnow() - timedelta(days=days)
    fav_rows = db.execute(
        select(task_collections.c.created_at).where(task_collections.c.created_at >= since)
    ).all()
    dl_rows = db.execute(
        select(Download.created_at).where(Download.created_at >= since)
    ).all()

    days_map: dict[str, dict] = {}
    for (ts,) in fav_rows:
        if ts:
            d = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
            days_map.setdefault(d, {"favorites": 0, "downloads": 0})["favorites"] += 1
    for (ts,) in dl_rows:
        if ts:
            d = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
            days_map.setdefault(d, {"favorites": 0, "downloads": 0})["downloads"] += 1
    return {"days": days_map, "total_days": days}



# F8: 个性化推荐（收藏/已看偏好 → 未看作品打分；5 分钟缓存）
_recommend_cache: dict = {"ts": 0.0, "data": None}
_RECOMMEND_TTL = 300.0


@router.get("/recommendations")
def recommendations(db: DbSession, _user: CurrentUser, limit: int = Query(6, le=20)):
    """个性化推荐：从收藏与已看作品聚合演员/标签/厂商偏好，
    对未看作品打分排序（偏好权重 + 评分加成）。"""
    import time
    from collections import Counter
    from models import Task, task_collections

    now = time.monotonic()
    if _recommend_cache["data"] is not None and now - _recommend_cache["ts"] < _RECOMMEND_TTL:
        return _recommend_cache["data"]

    # 已收藏 + 已看/想看的 task id
    fav_ids = {r[0] for r in db.execute(select(task_collections.c.task_id)).all()}
    viewed_ids = {r[0] for r in db.execute(
        select(Task.id).where(Task.view_status.in_(["viewed", "want"]))
    ).all()}
    known = fav_ids | viewed_ids

    # 偏好画像
    pref_rows = db.execute(
        select(Task.actors, Task.tags, Task.maker).where(Task.id.in_(known))
    ).all() if known else []
    actor_pref: Counter = Counter()
    tag_pref: Counter = Counter()
    maker_pref: Counter = Counter()
    for actors, tags, maker in pref_rows:
        for a in (actors or "").split(","):
            if a.strip():
                actor_pref[a.strip()] += 1
        for t in (tags or "").split(","):
            if t.strip():
                tag_pref[t.strip()] += 1
        if maker:
            maker_pref[maker] += 1

    if not (actor_pref or tag_pref or maker_pref):
        # 无偏好：返回高分未看
        rows = db.execute(
            select(Task).where(
                Task.rating.isnot(None), Task.video_code.isnot(None),
                ~Task.id.in_(known) if known else Task.id.isnot(None),
            ).order_by(Task.rating.desc()).limit(limit)
        ).scalars().all()
        result = {"items": [
            {"task_id": t.id, "video_code": t.video_code, "title": t.title,
             "rating": t.rating, "poster_url": t.poster_url, "score": None, "match": []}
            for t in rows
        ], "reason": "no_pref"}
        _recommend_cache["ts"] = now
        _recommend_cache["data"] = result
        return result

    # 候选：未收藏未看的任务（限定有元数据的）
    candidates = db.execute(
        select(Task).where(
            ~Task.id.in_(known),
            Task.video_code.isnot(None),
            (Task.actors.isnot(None) | Task.tags.isnot(None) | Task.rating.isnot(None)),
        )
    ).scalars().all()

    scored = []
    for t in candidates:
        t_actors = set(a.strip() for a in (t.actors or "").split(",") if a.strip())
        t_tags = set(a.strip() for a in (t.tags or "").split(",") if a.strip())
        score = 0.0
        score += sum(actor_pref[a] for a in t_actors) * 2.0
        score += sum(tag_pref[a] for a in t_tags) * 1.0
        if t.maker and maker_pref.get(t.maker):
            score += maker_pref[t.maker] * 1.5
        if t.rating:
            score += (t.rating - 5) * 0.5
        scored.append((score, t))

    scored.sort(key=lambda x: x[0], reverse=True)
    items = []
    for score, t in scored[:limit]:
        t_actors = set(a.strip() for a in (t.actors or "").split(",") if a.strip())
        t_tags = set(a.strip() for a in (t.tags or "").split(",") if a.strip())
        match = [a for a in t_actors if actor_pref[a]][:3] + [a for a in t_tags if tag_pref[a]][:3]
        items.append({
            "task_id": t.id, "video_code": t.video_code, "title": t.title,
            "rating": t.rating, "poster_url": t.poster_url, "score": round(score, 1),
            "match": match[:4],
        })
    result = {"items": items, "reason": "pref"}
    _recommend_cache["ts"] = now
    _recommend_cache["data"] = result
    return result
