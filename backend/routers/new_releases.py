"""新作品路由 —— 列出/标记已读/入库/立即巡检。

暴露 new_releases 表数据，让前端能看到订阅演员的新作品并操作。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from deps import CurrentUser, DbSession
from models import Actor, NewRelease

router = APIRouter(prefix="/api/new-releases", tags=["new-releases"])


@router.get("")
def list_new_releases(
    db: DbSession,
    _user: CurrentUser,
    actor_id: int | None = Query(None),
    unread_only: bool = Query(False),
    limit: int = Query(100, le=500),
):
    """列出新作品（可按演员筛选/只看未读）。"""
    stmt = select(NewRelease, Actor.name).outerjoin(Actor, NewRelease.actor_id == Actor.id)
    if actor_id is not None:
        stmt = stmt.where(NewRelease.actor_id == actor_id)
    if unread_only:
        stmt = stmt.where(NewRelease.is_read == False)  # noqa: E712
    stmt = stmt.order_by(NewRelease.discovered_at.desc()).limit(limit)
    rows = db.execute(stmt).all()
    return {
        "items": [
            {
                "id": nr.id,
                "actor_id": nr.actor_id,
                "actor_name": actor_name,
                "video_code": nr.video_code,
                "title": nr.title,
                "detail_url": nr.detail_url,
                "cover_url": nr.cover_url,
                "is_read": nr.is_read,
                "added_to_library": nr.added_to_library,
                "task_id": nr.task_id,
                "discovered_at": nr.discovered_at.isoformat() if nr.discovered_at else None,
            }
            for nr, actor_name in rows
        ],
        "total": len(rows),
    }


@router.post("/{new_release_id}/read")
def mark_read_api(new_release_id: int, db: DbSession, _user: CurrentUser):
    """标记新作品为已读。"""
    from services.new_works_monitor import mark_read

    nr = db.get(NewRelease, new_release_id)
    if not nr:
        raise HTTPException(status_code=404, detail="新作品不存在")
    mark_read(new_release_id, db)
    db.commit()
    return {"ok": True, "message": "已标记已读"}


@router.post("/{new_release_id}/add-to-library")
def add_to_library_api(new_release_id: int, db: DbSession, _user: CurrentUser):
    """手动入库（创建 pending task，等 scraper 处理）。"""
    from services.new_works_monitor import add_to_library

    nr = db.get(NewRelease, new_release_id)
    if not nr:
        raise HTTPException(status_code=404, detail="新作品不存在")
    task_id = add_to_library(new_release_id, db)
    db.commit()
    if task_id:
        return {"ok": True, "message": "已入库", "task_id": task_id}
    return {"ok": True, "message": "该作品已入库"}


@router.post("/check-now/{actor_id}")
async def check_now(actor_id: int, db: DbSession, _user: CurrentUser):
    """立即巡检某演员（手动触发，不等 6h 定时）。

    走完整的 check_actor_new_works 流程：抓 javdb → 去重 → Emby 比对 → 通知。
    auto_add 取该演员的订阅配置（若有订阅且 auto_add=True）。
    """
    from services.new_works_monitor import check_actor_new_works
    from models import Subscription

    # 查该演员的订阅，取 auto_add 设置
    sub = db.execute(
        select(Subscription).where(
            Subscription.actor_id == actor_id,
            Subscription.sub_type == "actor",
        )
    ).scalar_one_or_none()
    auto_add = sub.auto_add if sub else False

    result = await check_actor_new_works(actor_id, auto_add=auto_add)
    return {"ok": True, "result": result}


@router.post("/check-all")
async def check_all_now(_user: CurrentUser):
    """立即巡检所有订阅演员（手动触发）。"""
    from services.new_works_monitor import run_check_all

    result = await run_check_all()
    return {"ok": True, "result": result}
