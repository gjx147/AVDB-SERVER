"""收藏分组路由 —— RESTful 规范化（取代 AVDB 的 /api/tasks/{id}/favorite）。

- GET /api/favorites: 收藏的任务列表
- GET/POST/DELETE /api/collections: 分组 CRUD
- POST/DELETE /api/collections/{id}/tasks/{task_id}: 分组内增删任务
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from deps import CurrentUser, DbSession, Pagination
from models import Collection, Task, task_collections
from schemas import TaskListResponse

router = APIRouter(tags=["favorites"])


# ── 收藏任务（基于 Task.is_favorite）──
@router.get("/api/favorites", response_model=TaskListResponse)
def list_favorites(db: DbSession, _user: CurrentUser, pagination: Pagination):
    from sqlalchemy import func
    stmt = select(Task).where(Task.is_favorite == True)  # noqa: E712
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    offset, limit = pagination
    items = db.execute(stmt.order_by(Task.favorite_at.desc()).offset(offset).limit(limit)).scalars().all()
    return TaskListResponse(total=total, page=offset // limit + 1, page_size=limit, items=items)


# ── 分组 CRUD ──
class CollectionCreate(BaseModel):
    name: str = Field(max_length=100)
    description: str | None = Field(default=None, max_length=500)


@router.get("/api/collections")
def list_collections(db: DbSession, _user: CurrentUser):
    cols = db.execute(select(Collection).order_by(Collection.id)).scalars().all()
    return [{"id": c.id, "name": c.name, "description": c.description, "task_count": len(c.tasks)} for c in cols]


@router.post("/api/collections", status_code=201)
def create_collection(payload: CollectionCreate, db: DbSession, _user: CurrentUser):
    c = Collection(name=payload.name, description=payload.description)
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"id": c.id, "name": c.name, "description": c.description}


@router.delete("/api/collections/{collection_id}")
def delete_collection(collection_id: int, db: DbSession, _user: CurrentUser):
    c = db.get(Collection, collection_id)
    if not c:
        raise HTTPException(status_code=404, detail="分组不存在")
    db.delete(c)
    db.commit()
    return {"ok": True}


@router.get("/api/collections/{collection_id}/tasks", response_model=TaskListResponse)
def list_collection_tasks(collection_id: int, db: DbSession, _user: CurrentUser, pagination: Pagination):
    c = db.get(Collection, collection_id)
    if not c:
        raise HTTPException(status_code=404, detail="分组不存在")
    tasks = c.tasks
    offset, limit = pagination
    total = len(tasks)
    page_items = tasks[offset:offset + limit]
    return TaskListResponse(total=total, page=offset // limit + 1, page_size=limit, items=page_items)


@router.post("/api/collections/{collection_id}/tasks/{task_id}")
def add_task_to_collection(collection_id: int, task_id: int, db: DbSession, _user: CurrentUser):
    c = db.get(Collection, collection_id)
    t = db.get(Task, task_id)
    if not c or not t:
        raise HTTPException(status_code=404, detail="分组或任务不存在")
    if t not in c.tasks:
        c.tasks.append(t)
        db.commit()
    return {"ok": True}


@router.delete("/api/collections/{collection_id}/tasks/{task_id}")
def remove_task_from_collection(collection_id: int, task_id: int, db: DbSession, _user: CurrentUser):
    c = db.get(Collection, collection_id)
    t = db.get(Task, task_id)
    if not c or not t:
        raise HTTPException(status_code=404, detail="分组或任务不存在")
    if t in c.tasks:
        c.tasks.remove(t)
        db.commit()
    return {"ok": True}
