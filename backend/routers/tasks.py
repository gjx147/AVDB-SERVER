"""任务管理路由 —— 列表/详情/统计/删除（含级联）。"""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from deps import CurrentUser, DbSession, Pagination
from models import Task, Actor
from schemas import TaskListResponse, TaskOut

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


# ── 静态路由（必须在 /{task_id} 之前！）──

@router.get("", response_model=TaskListResponse)
def list_tasks(
    db: DbSession, _user: CurrentUser, pagination: Pagination,
    list_source_id: int | None = Query(None), status: str | None = Query(None),
    view_status: str | None = Query(None), is_favorite: bool | None = Query(None),
):
    stmt = select(Task); count_stmt = select(func.count(Task.id))
    if list_source_id is not None: stmt = stmt.where(Task.list_source_id == list_source_id); count_stmt = count_stmt.where(Task.list_source_id == list_source_id)
    if status: stmt = stmt.where(Task.status == status); count_stmt = count_stmt.where(Task.status == status)
    if view_status: stmt = stmt.where(Task.view_status == view_status); count_stmt = count_stmt.where(Task.view_status == view_status)
    if is_favorite is not None: stmt = stmt.where(Task.is_favorite == is_favorite); count_stmt = count_stmt.where(Task.is_favorite == is_favorite)
    offset, limit = pagination
    total = db.execute(count_stmt).scalar_one()
    items = db.execute(stmt.order_by(Task.id.desc()).offset(offset).limit(limit)).scalars().all()
    return TaskListResponse(total=total, page=offset//limit+1, page_size=limit, items=items)


@router.get("/stats")
def task_stats(db: DbSession, _user: CurrentUser):
    rows = db.execute(select(Task.status, func.count(Task.id)).group_by(Task.status)).all()
    by_status = {r[0]: r[1] for r in rows}
    total = sum(by_status.values())
    viewed = db.execute(select(func.count(Task.id)).where(Task.view_status == "viewed")).scalar_one()
    favorite = db.execute(select(func.count(Task.id)).where(Task.is_favorite == True)).scalar_one()  # noqa: E712
    return {"total": total, "by_status": by_status, "viewed": viewed, "favorite": favorite}


@router.post("/batch-delete")
@router.post("/batch/delete")  # 兼容前端旧路径
def batch_delete(task_ids: list[int], db: DbSession, _user: CurrentUser):
    if not task_ids: return {"ok": True, "deleted": 0}
    deleted = db.execute(Task.__table__.delete().where(Task.id.in_(task_ids))).rowcount
    db.commit()
    return {"ok": True, "deleted": deleted}


@router.post("/batch/retry")
def batch_retry(task_ids: list[int], db: DbSession, _user: CurrentUser):
    """批量重置失败任务为 pending。"""
    updated = db.execute(
        Task.__table__.update().where(
            Task.id.in_(task_ids), Task.status == "failed"
        ).values(status="pending", retry_count=0)
    ).rowcount
    db.commit()
    return {"ok": True, "updated": updated}


@router.post("/batch/favorite")
def batch_favorite(task_ids: list[int], db: DbSession, _user: CurrentUser):
    """批量设为收藏。"""
    from datetime import datetime
    updated = db.execute(
        Task.__table__.update().where(Task.id.in_(task_ids))
        .values(is_favorite=True, favorite_at=datetime.utcnow())
    ).rowcount
    db.commit()
    return {"ok": True, "updated": updated}


@router.get("/search")
def search_tasks(db: DbSession, _user: CurrentUser, q: str = Query(..., min_length=1),
                 status: str | None = Query(None), skip: int = Query(0, ge=0), limit: int = Query(48, ge=1, le=200)):
    stmt = select(Task).where(or_(Task.title.like(f"%{q}%"), Task.video_code.like(f"%{q}%")))
    if status: stmt = stmt.where(Task.status == status)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = db.execute(stmt.order_by(Task.id.desc()).offset(skip).limit(limit)).scalars().all()
    return {"total": total, "items": items, "q": q}


@router.get("/search/count")
def search_count(db: DbSession, _user: CurrentUser, q: str = Query(..., min_length=1), status: str | None = Query(None)):
    stmt = select(func.count(Task.id)).where(or_(Task.title.like(f"%{q}%"), Task.video_code.like(f"%{q}%")))
    if status: stmt = stmt.where(Task.status == status)
    return {"count": db.execute(stmt).scalar_one()}


@router.get("/favorites/list")
def list_favorites_tasks(db: DbSession, _user: CurrentUser, skip: int = Query(0, ge=0), limit: int = Query(48, ge=1, le=200)):
    stmt = select(Task).where(Task.is_favorite == True)  # noqa: E712
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = db.execute(stmt.order_by(Task.favorite_at.desc().nullslast(), Task.id.desc()).offset(skip).limit(limit)).scalars().all()
    return {"total": total, "items": items}


# ── 动态路由 /{task_id} 及其子路由 ──

@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: DbSession, _user: CurrentUser):
    task = db.get(Task, task_id)
    if not task: raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/{task_id}/cast")
def task_cast(task_id: int, db: DbSession, _user: CurrentUser):
    """返回 task 的关联演员 [{id, name, avatar_url}]，按名字查 actors 表。

    task.actors 是逗号分隔的名字字符串，这里批量匹配 actors 表拿头像。
    匹配策略：精确 name 批量查 → 未命中的 LIKE 兜底。避免 N+1 查询。
    """
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not task.actors:
        return []
    names = [n.strip() for n in task.actors.split(",") if n.strip()][:15]
    if not names:
        return []
    # 批量精确查询（1 次 SQL 替代最多 15 次）
    exact_rows = db.execute(select(Actor).where(Actor.name.in_(names))).scalars().all()
    exact_map = {a.name: a for a in exact_rows}
    # 对未精确命中的名字做 LIKE 兜底（每个一次，但通常很少）
    missing = [n for n in names if n not in exact_map]
    for name in missing:
        actor = db.execute(
            select(Actor).where(Actor.name.like(f"%{name}%")).limit(1)
        ).scalar_one_or_none()
        if actor:
            exact_map[name] = actor
    # 按原始顺序返回
    result = []
    for name in names:
        actor = exact_map.get(name)
        if actor:
            result.append({"id": actor.id, "name": actor.name, "avatar_url": actor.avatar_url})
        else:
            result.append({"id": None, "name": name, "avatar_url": None})
    return result


@router.post("/{task_id}/extract")
def extract_single(task_id: int, db: DbSession, _user: CurrentUser):
    """触发单任务提取（fire-and-forget subprocess）。"""
    import asyncio, os, sys, subprocess
    task = db.get(Task, task_id)
    if not task: raise HTTPException(status_code=404, detail="任务不存在")
    # 异步触发 scraper extract-single
    from config import get_settings
    settings = get_settings()
    scraper = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "magnet_scraper", "scraper.py")
    python = settings.SCRAPER_PYTHON or sys.executable
    try:
        subprocess.Popen([python, scraper, "extract-single", "--url", task.url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=dict(os.environ))
    except Exception as e:
        return {"ok": False, "message": str(e)}
    return {"ok": True, "message": "已触发提取"}


@router.delete("/{task_id}")
def delete_task(task_id: int, db: DbSession, _user: CurrentUser):
    task = db.get(Task, task_id)
    if not task: raise HTTPException(status_code=404, detail="任务不存在")
    db.delete(task); db.commit()
    return {"ok": True, "message": "已删除"}


@router.post("/{task_id}/favorite")
def toggle_favorite(task_id: int, db: DbSession, _user: CurrentUser):
    task = db.get(Task, task_id)
    if not task: raise HTTPException(status_code=404, detail="任务不存在")
    task.is_favorite = not task.is_favorite
    task.favorite_at = datetime.utcnow() if task.is_favorite else None
    db.commit()
    return {"ok": True, "is_favorite": task.is_favorite}


@router.patch("/{task_id}/view-status")
def set_view_status(task_id: int, status: str, db: DbSession, _user: CurrentUser):
    valid = {"viewed", "browsed", "want", ""}
    if status not in valid: raise HTTPException(status_code=400, detail=f"无效状态，可选: {valid - {''}}")
    task = db.get(Task, task_id)
    if not task: raise HTTPException(status_code=404, detail="任务不存在")
    task.view_status = status or None
    task.viewed_at = datetime.utcnow() if status == "viewed" else task.viewed_at
    db.commit()
    return {"ok": True, "view_status": task.view_status}


@router.get("/{task_id}/magnets")
def get_magnets(task_id: int, db: DbSession, _user: CurrentUser):
    task = db.get(Task, task_id)
    if not task: raise HTTPException(status_code=404, detail="任务不存在")
    magnets = []
    if task.magnets_json:
        try:
            raw = json.loads(task.magnets_json)
            if isinstance(raw, list): magnets = raw
        except json.JSONDecodeError: pass
    if not magnets and task.best_magnet: magnets = [task.best_magnet]
    return {"magnets": magnets, "video_code": task.video_code}


@router.patch("/{task_id}/note")
def update_note(task_id: int, db: DbSession, _user: CurrentUser, note: str = Query("")):
    task = db.get(Task, task_id)
    if not task: raise HTTPException(status_code=404, detail="任务不存在")
    task.note = note or None
    db.commit()
    return {"ok": True}
