"""多维订阅路由 —— Immortal 式订阅体系 CRUD。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from deps import CurrentUser, DbSession
from models import Subscription
from schemas import SubscriptionCreate, SubscriptionOut

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])

VALID_TYPES = {"ranking", "actor", "composite"}


class FillAllWorksRequest(BaseModel):
    """全部补齐作品：每演员等待上限（分钟，可选）。"""
    wait_limit_min: int | None = None


@router.post("/fill-all-works")
def start_fill_all_works(payload: FillAllWorksRequest, _user: CurrentUser):
    """启动「全部补齐作品」后台任务（串行爬取所有订阅演员的作品）。"""
    from services import actor_works_batch
    ok, msg = actor_works_batch.start(payload.wait_limit_min or 60)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"ok": True, "message": msg}


@router.get("/fill-works-status")
def fill_works_status(_user: CurrentUser):
    """「全部补齐作品」任务进度（前端轮询；切走页面任务继续跑）。"""
    from services import actor_works_batch
    return actor_works_batch.status()


@router.get("", response_model=list[SubscriptionOut])
def list_subscriptions(db: DbSession, _user: CurrentUser, enabled: bool | None = None):
    """列出所有订阅，可按 enabled 筛选。"""
    stmt = select(Subscription).order_by(Subscription.id)
    if enabled is not None:
        stmt = stmt.where(Subscription.enabled == enabled)
    return db.execute(stmt).scalars().all()


@router.post("", response_model=SubscriptionOut, status_code=201)
def create_subscription(payload: SubscriptionCreate, db: DbSession, _user: CurrentUser):
    """创建订阅。"""
    if payload.sub_type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail=f"无效类型，可选: {VALID_TYPES}")
    if payload.sub_type == "ranking" and not payload.rank_type:
        raise HTTPException(status_code=400, detail="ranking 类型需指定 rank_type")
    if payload.sub_type == "actor" and not payload.actor_id:
        raise HTTPException(status_code=400, detail="actor 类型需指定 actor_id")
    sub = Subscription(**payload.model_dump())
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


@router.get("/{subscription_id}", response_model=SubscriptionOut)
def get_subscription(subscription_id: int, db: DbSession, _user: CurrentUser):
    sub = db.get(Subscription, subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    return sub


@router.put("/{subscription_id}", response_model=SubscriptionOut)
def update_subscription(subscription_id: int, payload: SubscriptionCreate, db: DbSession, _user: CurrentUser):
    sub = db.get(Subscription, subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    for k, v in payload.model_dump().items():
        setattr(sub, k, v)
    db.commit()
    db.refresh(sub)
    return sub


@router.delete("/{subscription_id}")
def delete_subscription(subscription_id: int, db: DbSession, _user: CurrentUser):
    sub = db.get(Subscription, subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    db.delete(sub)
    db.commit()
    return {"ok": True, "message": "已删除"}


@router.post("/{subscription_id}/toggle")
def toggle_subscription(subscription_id: int, db: DbSession, _user: CurrentUser):
    """启用/停用订阅。"""
    sub = db.get(Subscription, subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    sub.enabled = not sub.enabled
    db.commit()
    return {"ok": True, "enabled": sub.enabled}
