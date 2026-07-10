"""演员库路由 —— 档案/列表筛选/关注/拉黑/详情(含关联作品)。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select

from deps import CurrentUser, DbSession, Pagination
from models import Actor, Task, actor_movies
from schemas import ActorDetailOut, ActorListResponse, ActorOut

router = APIRouter(prefix="/api/actors", tags=["actors"])


@router.get("", response_model=ActorListResponse)
def list_actors(
    db: DbSession,
    _user: CurrentUser,
    pagination: Pagination,
    q: str | None = Query(None, description="按名字搜索"),
    followed: bool | None = Query(None, description="只看关注的"),
    blacklisted: bool | None = Query(None, description="只看拉黑的"),
):
    """演员列表，支持名字搜索 + 关注/拉黑筛选 + 分页。"""
    stmt = select(Actor)
    count_stmt = select(func.count(Actor.id))
    if q:
        like = f"%{q}%"
        cond = or_(Actor.name.like(like), Actor.name_en.like(like))
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    if followed is not None:
        stmt = stmt.where(Actor.is_followed == followed)
        count_stmt = count_stmt.where(Actor.is_followed == followed)
    if blacklisted is not None:
        stmt = stmt.where(Actor.is_blacklisted == blacklisted)
        count_stmt = count_stmt.where(Actor.is_blacklisted == blacklisted)

    offset, limit = pagination
    total = db.execute(count_stmt).scalar_one()
    items = (
        db.execute(stmt.order_by(Actor.is_followed.desc(), Actor.id.desc()).offset(offset).limit(limit))
        .scalars()
        .all()
    )
    return ActorListResponse(total=total, page=offset // limit + 1, page_size=limit, items=items)


@router.get("/{actor_id}", response_model=ActorDetailOut)
def get_actor(actor_id: int, db: DbSession, _user: CurrentUser):
    """演员详情，含关联作品 ID 列表。"""
    actor = db.get(Actor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    movie_ids = [
        r[0]
        for r in db.execute(
            select(actor_movies.c.task_id).where(actor_movies.c.actor_id == actor_id)
        ).all()
    ]
    return ActorDetailOut(
        **{c.name: getattr(actor, c.name) for c in actor.__table__.columns},
        movie_ids=movie_ids,
    )


@router.post("/{actor_id}/follow")
def toggle_follow(actor_id: int, db: DbSession, _user: CurrentUser):
    """切换关注状态。"""
    actor = db.get(Actor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    actor.is_followed = not actor.is_followed
    db.commit()
    return {"ok": True, "is_followed": actor.is_followed, "actor_id": actor_id}


@router.post("/{actor_id}/unfollow")
def unfollow(actor_id: int, db: DbSession, _user: CurrentUser):
    """取消关注（兼容前端独立 unfollow 调用）。"""
    actor = db.get(Actor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    actor.is_followed = False
    db.commit()
    return {"ok": True, "is_followed": False, "actor_id": actor_id}


@router.post("/{actor_id}/blacklist")
def toggle_blacklist(actor_id: int, db: DbSession, _user: CurrentUser):
    """切换拉黑状态。"""
    actor = db.get(Actor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    actor.is_blacklisted = not actor.is_blacklisted
    db.commit()
    return {"ok": True, "is_blacklisted": actor.is_blacklisted}


@router.delete("/{actor_id}")
def delete_actor(actor_id: int, db: DbSession, _user: CurrentUser):
    """删除演员（actor_movies 由 ON DELETE CASCADE 自动清理）。"""
    actor = db.get(Actor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    db.delete(actor)
    db.commit()
    return {"ok": True, "message": "已删除"}


@router.get("/{actor_id}/movies", response_model=list)
def actor_movies_list(actor_id: int, db: DbSession, _user: CurrentUser, limit: int = Query(50, le=200)):
    """演员的关联作品列表。"""
    actor = db.get(Actor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    task_ids = [
        r[0]
        for r in db.execute(
            select(actor_movies.c.task_id).where(actor_movies.c.actor_id == actor_id).limit(limit)
        ).all()
    ]
    if not task_ids:
        return []
    tasks = db.execute(select(Task).where(Task.id.in_(task_ids)).order_by(Task.id.desc())).scalars().all()
    return [{"id": t.id, "video_code": t.video_code, "title": t.title, "status": t.status} for t in tasks]
