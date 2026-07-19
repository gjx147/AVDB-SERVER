"""排行榜路由 —— 按类型查询榜单 + 批量入库。

路由修复：/types/dates 静态路由必须在 /{rank_type} 动态路由之前定义。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from deps import CurrentUser, DbSession, Pagination
from models import ListSource, Ranking, Task, Setting
from schemas import BatchAddTasksRequest, RankingOut

router = APIRouter(prefix="/api/rankings", tags=["rankings"])

VALID_TYPES = {"daily", "weekly", "monthly", "actor"}


def _get_javdb_url(db) -> str:
    """从 DB settings 读 javdb_url（与 scraper 子进程一致），fallback 到 config 默认值。"""
    row = db.get(Setting, "javdb_url")
    if row and row.value and row.value.strip():
        return row.value.strip().rstrip("/")
    from config import get_settings
    return (get_settings().JAVDB_URL or "https://javdb.com").rstrip("/")


def _enrich_with_task(db, rankings: list) -> list:
    """给 ranking 列表补充关联 task 的真实数据（番号/标题/海报/缩略图/状态）。

    避免 N+1：先批量查所有 task_id，再一次性取数据。
    返回 dict 列表（RankingOut 兼容）。
    """
    if not rankings:
        return rankings
    task_ids = [r.task_id for r in rankings if r.task_id]
    tasks_map = {}
    if task_ids:
        for t in db.execute(select(Task).where(Task.id.in_(task_ids))).scalars().all():
            tasks_map[t.id] = t
    result = []
    for r in rankings:
        item = {
            "id": r.id, "rank_type": r.rank_type, "rank_date": r.rank_date,
            "rank_position": r.rank_position, "video_code": r.video_code,
            "title": r.title, "cover_url": r.cover_url, "score": r.score,
            "views": r.views or 0, "detail_url": r.detail_url,
            "task_id": r.task_id, "is_in_library": r.is_in_library,
            "created_at": r.created_at,
        }
        t = tasks_map.get(r.task_id)
        if t:
            item["task_video_code"] = t.video_code
            item["task_title"] = t.title
            item["task_poster_url"] = t.poster_url
            item["task_thumbnail_urls"] = t.thumbnail_urls
            item["task_status"] = t.status
        result.append(item)
    return result


# ── 静态路由必须在动态路由之前 ──

@router.get("/types/dates")
def list_dates(db: DbSession, _user: CurrentUser, rank_type: str | None = Query(None)):
    """列出有数据的排行榜日期（用于前端切换日期）。"""
    stmt = select(Ranking.rank_type, Ranking.rank_date).distinct()
    if rank_type:
        stmt = stmt.where(Ranking.rank_type == rank_type)
    rows = db.execute(stmt.order_by(Ranking.rank_date.desc())).all()
    result: dict[str, list[str]] = {}
    for t, d in rows:
        result.setdefault(t, []).append(d)
    return result


# ── 兼容 AVDB 原始格式：GET /api/rankings?rank_type=X&skip=Y&limit=Z ──

@router.get("/latest")
def latest_rankings(db: DbSession, _user: CurrentUser, rank_type: str = Query("daily")):
    """获取最新一天的排行榜（兼容前端 /api/rankings/latest）。"""
    latest_date = db.execute(
        select(func.max(Ranking.rank_date)).where(Ranking.rank_type == rank_type)
    ).scalar_one()
    if not latest_date:
        return {"rankings": [], "rank_date": None}
    rows = db.execute(
        select(Ranking).where(Ranking.rank_type == rank_type, Ranking.rank_date == latest_date)
        .order_by(Ranking.rank_position)
    ).scalars().all()
    return {"rankings": rows, "rank_date": latest_date}


@router.post("/{ranking_id}/add-task")
def add_single_task(ranking_id: int, db: DbSession, _user: CurrentUser):
    """单条排行榜入库为 task。"""
    r = db.get(Ranking, ranking_id)
    if not r:
        raise HTTPException(status_code=404, detail="排行条目不存在")
    if not r.video_code:
        return {"ok": False, "message": "无番号"}

    # 从 DB 读 javdb_url（与 scraper 子进程一致），避免镜像站 URL 不一致导致重复
    base_url = _get_javdb_url(db)
    full_url = f"{base_url}/v/{r.video_code}"

    # 按 URL 去重
    existing = db.execute(select(Task).where(Task.url.in_([full_url, f"/v/{r.video_code}"]))).scalar_one_or_none()
    if existing:
        r.task_id = existing.id
        r.is_in_library = True
        db.commit()
        return {"ok": True, "task_id": existing.id, "ranking_id": ranking_id}
    src = db.execute(select(ListSource).where(ListSource.list_code == "RANKING")).scalar_one_or_none()
    if not src:
        src = ListSource(list_code="RANKING", list_path="/rankings")
        db.add(src); db.flush()
    t = Task(list_source_id=src.id, url=full_url, video_code=r.video_code)
    db.add(t)
    try:
        db.flush()
    except Exception:
        db.rollback()
        # 并发时可能已被创建，重新查找
        existing = db.execute(select(Task).where(Task.url == full_url)).scalar_one_or_none()
        if existing:
            r.task_id = existing.id
            r.is_in_library = True
            db.commit()
            return {"ok": True, "task_id": existing.id, "ranking_id": ranking_id}
        raise
    r.task_id = t.id
    r.is_in_library = True
    db.commit()
    return {"ok": True, "task_id": t.id, "ranking_id": ranking_id}


@router.get("", response_model=list[RankingOut])
def list_rankings_compat(
    db: DbSession,
    _user: CurrentUser,
    rank_type: str | None = Query(None),
    rank_date: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
):
    """兼容 AVDB 前端：按 type/date 查询排行榜，支持分页。"""
    stmt = select(Ranking)
    if rank_type and rank_type in VALID_TYPES:
        stmt = stmt.where(Ranking.rank_type == rank_type)
        if not rank_date:
            # 取最新日期
            latest = db.execute(
                select(func.max(Ranking.rank_date)).where(Ranking.rank_type == rank_type)
            ).scalar_one()
            if not latest:
                return []
            rank_date = latest
    if rank_date:
        stmt = stmt.where(Ranking.rank_date == rank_date)
    rows = db.execute(stmt.order_by(Ranking.rank_position).offset(skip).limit(limit)).scalars().all()
    return _enrich_with_task(db, rows)


# ── 动态路由 /{rank_type} ──

@router.get("/{rank_type}", response_model=list[RankingOut])
def list_by_type(
    rank_type: str,
    db: DbSession,
    _user: CurrentUser,
    date: str | None = Query(None, description="指定日期(YYYY-MM-DD)，默认最新"),
):
    """按类型查询排行榜。不传 date 返回最新一天的。"""
    if rank_type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail=f"无效类型，可选: {VALID_TYPES}")
    if date:
        rows = (
            db.execute(
                select(Ranking)
                .where(Ranking.rank_type == rank_type, Ranking.rank_date == date)
                .order_by(Ranking.rank_position)
            )
            .scalars()
            .all()
        )
        return _enrich_with_task(db, rows)
    latest_date = db.execute(
        select(func.max(Ranking.rank_date)).where(Ranking.rank_type == rank_type)
    ).scalar_one()
    if not latest_date:
        return []
    rows = (
        db.execute(
            select(Ranking)
            .where(Ranking.rank_type == rank_type, Ranking.rank_date == latest_date)
            .order_by(Ranking.rank_position)
        )
        .scalars()
        .all()
    )
    return _enrich_with_task(db, rows)


@router.post("/batch-add-tasks")
def batch_add_tasks(req: BatchAddTasksRequest, db: DbSession, _user: CurrentUser):
    """批量把排行榜条目入库为 pending task（幂等：按 URL 去重）。"""
    if not req.ranking_ids:
        return {"ok": True, "added": 0, "skipped": 0}
    rankings = db.execute(
        select(Ranking).where(Ranking.id.in_(req.ranking_ids))
    ).scalars().all()

    # 从 DB 读 javdb_url（与 scraper 子进程一致）
    base_url = _get_javdb_url(db)

    src = db.execute(select(ListSource).where(ListSource.list_code == "RANKING")).scalar_one_or_none()
    if not src:
        src = ListSource(list_code="RANKING", list_path="/rankings")
        db.add(src)
        db.flush()

    added = 0
    skipped = 0
    for r in rankings:
        if not r.video_code:
            skipped += 1
            continue
        full_url = f"{base_url}/v/{r.video_code}"
        # 按 URL 去重（完整 URL 或相对路径都查）
        existing = db.execute(
            select(Task).where(Task.url.in_([full_url, f"/v/{r.video_code}"]))
        ).scalar_one_or_none()
        if existing:
            r.task_id = existing.id
            r.is_in_library = True
            skipped += 1
            continue
        t = Task(list_source_id=src.id, url=full_url, video_code=r.video_code)
        db.add(t)
        try:
            db.flush()
        except Exception:
            db.rollback()
            # 并发时可能已被创建，重新查找
            existing = db.execute(select(Task).where(Task.url == full_url)).scalar_one_or_none()
            if existing:
                r.task_id = existing.id
                r.is_in_library = True
                skipped += 1
                continue
            raise
        r.task_id = t.id
        r.is_in_library = True
        added += 1
    db.commit()
    # 返回前端期望的 results 数组格式
    results = []
    for r in rankings:
        results.append({
            "ranking_id": r.id,
            "task_id": r.task_id,
            "error": None if r.is_in_library else "跳过",
        })
    return {"ok": True, "added": added, "skipped": skipped, "results": results}


@router.delete("/{rank_type}/{date}")
def delete_ranking(rank_type: str, date: str, db: DbSession, _user: CurrentUser):
    """删除某类型某日的整张排行榜快照。"""
    if rank_type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail=f"无效类型")
    rows = db.execute(
        select(Ranking).where(Ranking.rank_type == rank_type, Ranking.rank_date == date)
    ).scalars().all()
    for r in rows:
        db.delete(r)
    db.commit()
    return {"ok": True, "deleted": len(rows)}
