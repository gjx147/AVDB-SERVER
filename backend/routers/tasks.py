"""任务管理路由 —— 列表/详情/统计/删除（含级联）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from deps import CurrentUser, DbSession, Pagination
from models import Task
from schemas import TaskListResponse, TaskOut

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("", response_model=TaskListResponse)
def list_tasks(
    db: DbSession,
    _user: CurrentUser,
    pagination: Pagination,
    list_source_id: int | None = Query(None),
    status: str | None = Query(None),
    view_status: str | None = Query(None),
    is_favorite: bool | None = Query(None),
):
    """任务列表，支持按 列表源/状态/观看状态/收藏 筛选 + 分页。"""
    stmt = select(Task)
    count_stmt = select(func.count(Task.id))
    if list_source_id is not None:
        stmt = stmt.where(Task.list_source_id == list_source_id)
        count_stmt = count_stmt.where(Task.list_source_id == list_source_id)
    if status:
        stmt = stmt.where(Task.status == status)
        count_stmt = count_stmt.where(Task.status == status)
    if view_status:
        stmt = stmt.where(Task.view_status == view_status)
        count_stmt = count_stmt.where(Task.view_status == view_status)
    if is_favorite is not None:
        stmt = stmt.where(Task.is_favorite == is_favorite)
        count_stmt = count_stmt.where(Task.is_favorite == is_favorite)

    offset, limit = pagination
    total = db.execute(count_stmt).scalar_one()
    items = (
        db.execute(stmt.order_by(Task.id.desc()).offset(offset).limit(limit))
        .scalars()
        .all()
    )
    return TaskListResponse(
        total=total, page=offset // limit + 1, page_size=limit, items=items
    )


@router.get("/stats")
def task_stats(db: DbSession, _user: CurrentUser):
    """任务统计：各状态计数。"""
    rows = db.execute(
        select(Task.status, func.count(Task.id)).group_by(Task.status)
    ).all()
    by_status = {r[0]: r[1] for r in rows}
    total = sum(by_status.values())
    viewed = db.execute(select(func.count(Task.id)).where(Task.view_status == "viewed")).scalar_one()
    favorite = db.execute(select(func.count(Task.id)).where(Task.is_favorite == True)).scalar_one()  # noqa: E712
    return {
        "total": total,
        "by_status": by_status,
        "viewed": viewed,
        "favorite": favorite,
    }


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: DbSession, _user: CurrentUser):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.delete("/{task_id}")
def delete_task(task_id: int, db: DbSession, _user: CurrentUser):
    """删除任务（actor_movies 由 ON DELETE CASCADE 自动级联）。"""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    db.delete(task)
    db.commit()
    return {"ok": True, "message": "已删除"}


@router.post("/batch-delete")
def batch_delete(task_ids: list[int], db: DbSession, _user: CurrentUser):
    """批量删除任务。"""
    if not task_ids:
        return {"ok": True, "deleted": 0}
    deleted = db.execute(Task.__table__.delete().where(Task.id.in_(task_ids))).rowcount  # type: ignore
    db.commit()
    return {"ok": True, "deleted": deleted}


@router.post("/{task_id}/favorite")
def toggle_favorite(task_id: int, db: DbSession, _user: CurrentUser):
    """切换收藏状态。"""
    from datetime import datetime
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    task.is_favorite = not task.is_favorite
    task.favorite_at = datetime.utcnow() if task.is_favorite else None
    db.commit()
    return {"ok": True, "is_favorite": task.is_favorite}


@router.patch("/{task_id}/view-status")
def set_view_status(task_id: int, status: str, db: DbSession, _user: CurrentUser):
    """设置观看状态（viewed/browsed/want），传空串清除。"""
    from datetime import datetime
    valid = {"viewed", "browsed", "want", ""}
    if status not in valid:
        raise HTTPException(status_code=400, detail=f"无效状态，可选: {valid - {''}}")
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    task.view_status = status or None
    task.viewed_at = datetime.utcnow() if status == "viewed" else task.viewed_at
    db.commit()
    return {"ok": True, "view_status": task.view_status}
