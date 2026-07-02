"""观看状态路由 —— viewed/browsed/want 三态标记（JavdBviewed 移植）。

与 tasks.py 的单任务 PATCH 互补，本路由提供：
- 按观看状态查询任务列表（/api/status/{status}）
- 批量设置观看状态（/api/status/batch）
- 观看状态统计（/api/status/stats）
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from deps import CurrentUser, DbSession, Pagination
from models import Task
from schemas import TaskListResponse

router = APIRouter(prefix="/api/status", tags=["view-status"])

VALID_STATUSES = {"viewed", "browsed", "want"}


class BatchStatusRequest(BaseModel):
    task_ids: list[int]
    status: str  # viewed/browsed/want，空串 "" 表示清除


@router.get("/stats")
def status_stats(db: DbSession, _user: CurrentUser):
    """各观看状态计数。"""
    rows = db.execute(
        select(Task.view_status, func.count(Task.id))
        .where(Task.view_status.isnot(None))
        .group_by(Task.view_status)
    ).all()
    by_status = {r[0]: r[1] for r in rows}
    total = db.execute(select(func.count(Task.id))).scalar_one()
    unmarked = db.execute(
        select(func.count(Task.id)).where(Task.view_status.is_(None))
    ).scalar_one()
    return {
        "total": total,
        "by_status": by_status,  # {"viewed": N, "browsed": N, "want": N}
        "unmarked": unmarked,
    }


@router.get("/{status}", response_model=TaskListResponse)
def list_by_status(
    status: str,
    db: DbSession,
    _user: CurrentUser,
    pagination: Pagination,
):
    """按观看状态查询任务列表。"""
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"无效状态，可选: {VALID_STATUSES}")
    stmt = select(Task).where(Task.view_status == status)
    count_stmt = select(func.count(Task.id)).where(Task.view_status == status)
    offset, limit = pagination
    total = db.execute(count_stmt).scalar_one()
    items = (
        db.execute(stmt.order_by(Task.viewed_at.desc().nullslast(), Task.id.desc()).offset(offset).limit(limit))
        .scalars()
        .all()
    )
    return TaskListResponse(total=total, page=offset // limit + 1, page_size=limit, items=items)


@router.post("/batch")
def batch_set_status(req: BatchStatusRequest, db: DbSession, _user: CurrentUser):
    """批量设置观看状态。status 传空串清除。"""
    if req.status and req.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"无效状态，可选: {VALID_STATUSES} 或空串清除")
    if not req.task_ids:
        return {"ok": True, "updated": 0}
    new_status = req.status or None
    now = datetime.utcnow() if req.status == "viewed" else None
    # 对已 viewed 的保留 viewed_at，新 viewed 设当前时间
    tasks = db.execute(select(Task).where(Task.id.in_(req.task_ids))).scalars().all()
    updated = 0
    for t in tasks:
        t.view_status = new_status
        if new_status == "viewed":
            t.viewed_at = now
        updated += 1
    db.commit()
    return {"ok": True, "updated": updated}
