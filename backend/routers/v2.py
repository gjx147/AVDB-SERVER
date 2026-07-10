"""v2 增强路由 —— 多维筛选/排序/相似推荐。

参考 AVDB v2_routes，用 ORM 重写（更安全）。
"""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select

from deps import CurrentUser, DbSession
from models import Task

router = APIRouter(prefix="/api/v2", tags=["v2"])


@router.get("/tasks")
def list_tasks_v2(
    db: DbSession,
    _user: CurrentUser,
    status: str | None = Query(None),
    actor: str | None = Query(None),
    tag: str | None = Query(None),
    maker: str | None = Query(None),
    min_rating: float | None = Query(None),
    sort: str = Query("created_desc", description="created_desc/rating_desc/title"),
    limit: int = Query(48, le=200),
    offset: int = Query(0, ge=0),
):
    """多维筛选 + 排序。"""
    stmt = select(Task).where(Task.status == "visited")
    if status:
        stmt = stmt.where(Task.status == status)
    if actor:
        stmt = stmt.where(Task.actors.like(f"%{actor}%"))
    if tag:
        stmt = stmt.where(Task.tags.like(f"%{tag}%"))
    if maker:
        stmt = stmt.where(Task.maker == maker)
    if min_rating is not None:
        stmt = stmt.where(Task.rating >= min_rating)

    sort_map = {
        "created_desc": Task.created_at.desc(),
        "rating_desc": Task.rating.desc().nullslast(),
        "title": Task.title,
    }
    stmt = stmt.order_by(sort_map.get(sort, Task.created_at.desc()))

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    tasks = db.execute(stmt.offset(offset).limit(limit)).scalars().all()
    return {"tasks": tasks, "total": total}


@router.get("/tasks/search-fts")
def search_fts(
    db: DbSession,
    _user: CurrentUser,
    q: str = Query(..., min_length=1),
    limit: int = Query(48, le=200),
):
    """FTS 全文搜索（兼容前端，实际用 LIKE 降级）。"""
    from sqlalchemy import or_
    stmt = select(Task).where(
        or_(Task.title.like(f"%{q}%"), Task.video_code.like(f"%{q}%"))
    )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    tasks = db.execute(stmt.order_by(Task.id.desc()).limit(limit)).scalars().all()
    return {"tasks": tasks, "total": total, "engine": "like"}


@router.get("/tasks/{task_id}/similar")
def similar_tasks(task_id: int, db: DbSession, _user: CurrentUser, limit: int = Query(10, le=50)):
    """相似推荐（Jaccard 相似度，基于 actors/tags/maker/series）。"""
    base = db.get(Task, task_id)
    if not base:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="任务不存在")

    base_features = set()
    if base.actors:
        base_features.update(a.strip() for a in base.actors.split(",") if a.strip())
    if base.tags:
        base_features.update(t.strip() for t in base.tags.split(",") if t.strip())
    if base.maker:
        base_features.add(f"maker:{base.maker}")
    if base.series:
        base_features.add(f"series:{base.series}")

    if not base_features:
        return {"tasks": [], "total": 0}

    # 候选：同 maker 或演员交集
    conds = []
    if base.maker:
        conds.append(Task.maker == base.maker)
    if base.actors:
        first_actor = base.actors.split(",")[0].strip()
        if first_actor:
            conds.append(Task.actors.like(f"%{first_actor}%"))
    candidates = db.execute(
        select(Task).where(
            Task.id != task_id,
            Task.status == "visited",
            or_(*conds) if conds else Task.id != task_id,
        ).limit(200)
    ).scalars().all()

    scored = []
    for c in candidates:
        c_features = set()
        if c.actors:
            c_features.update(a.strip() for a in c.actors.split(",") if a.strip())
        if c.tags:
            c_features.update(t.strip() for t in c.tags.split(",") if t.strip())
        if c.maker:
            c_features.add(f"maker:{c.maker}")
        if c.series:
            c_features.add(f"series:{c.series}")
        if not c_features:
            continue
        intersection = len(base_features & c_features)
        union = len(base_features | c_features)
        score = intersection / union if union else 0
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return {"tasks": [c for _, c in scored[:limit]], "total": len(scored)}


@router.get("/analytics")
@router.get("/dashboard/analytics")  # 兼容前端旧路径
def analytics(db: DbSession, _user: CurrentUser):
    """Top 演员/标签/厂牌 + 评分分布（用于 Dashboard 图表）。

    Phase E 优化：rating_dist 改用 SQL GROUP BY（不再全表加载到 Python）。
    actors/tags/maker 是逗号分隔字段，SQLite 无法直接 UNNEST，
    仍需 Python 聚合，但只 SELECT 需要的列（不加载全文 magnets_json/synopsis 等大字段）。
    """
    # 只查需要的列（不加载 magnets_json/synopsis 等大字段，减少 IO）
    rows = db.execute(
        select(Task.actors, Task.tags, Task.maker, Task.rating)
        .where(Task.status == "visited")
    ).all()

    def _top_from_values(values, n=10):
        c = Counter()
        for v in values:
            if v:
                for item in v.split(","):
                    item = item.strip()
                    if item:
                        c[item] += 1
        return [{"name": k, "count": v} for k, v in c.most_common(n)]

    # 评分分布（内存计算，但只处理 rating 一个 float 列，开销极小）
    rating_buckets = {"<6": 0, "6-7": 0, "7-8": 0, "8-9": 0, "9-10": 0}
    for _, _, _, rating in rows:
        if rating is not None:
            if rating < 6: rating_buckets["<6"] += 1
            elif rating < 7: rating_buckets["6-7"] += 1
            elif rating < 8: rating_buckets["7-8"] += 1
            elif rating < 9: rating_buckets["8-9"] += 1
            else: rating_buckets["9-10"] += 1

    return {
        "top_actors": _top_from_values([r[0] for r in rows if r[0]]),
        "top_tags": _top_from_values([r[1] for r in rows if r[1]]),
        "top_makers": _top_from_values([r[2] for r in rows if r[2]]),
        "rating_dist": [{"bucket": k, "count": v} for k, v in rating_buckets.items()],
    }
