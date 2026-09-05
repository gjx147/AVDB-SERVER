"""多维订阅路由 —— Immortal 式订阅体系 CRUD。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from deps import CurrentUser, DbSession
from models import Subscription, Task
import json
from schemas import SubscriptionCreate, SubscriptionOut

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])

VALID_TYPES = {"ranking", "actor", "composite"}


class FillAllWorksRequest(BaseModel):
    """全部补齐作品：每演员等待上限（分钟，可选）＋最大共演人数限制（可选，0=不限）＋发行日期下限（可选）。"""
    wait_limit_min: int | None = None
    max_co_star: int | None = None
    since: str = ""


class AiPreviewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class AiCreateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    name: str | None = Field(default=None, max_length=100)
    auto_add: bool = True
    check_interval_hours: int = 6


class FromTaskRequest(BaseModel):
    task_id: int
    name: str | None = Field(default=None, max_length=100)
    auto_add: bool = True
    check_interval_hours: int = 6


@router.post("/ai-preview")
async def ai_preview(req: AiPreviewRequest, db: DbSession, _user: CurrentUser):
    """S1: 自然语言 → 过滤条件 + 命中数预览（不创建）。"""
    from services.ai_subscription import count_matches, parse_filters_from_text
    filters, engine = await parse_filters_from_text(req.text)
    matched = count_matches(db, filters)
    return {"ok": True, "filters": filters, "engine": engine, "matched_count": matched}


@router.post("/ai-create")
async def ai_create(req: AiCreateRequest, db: DbSession, _user: CurrentUser):
    """S1: 自然语言创建 composite 订阅（AI 生成过滤条件）。"""
    from services.ai_subscription import count_matches, default_name, parse_filters_from_text
    filters, engine = await parse_filters_from_text(req.text)
    if not filters:
        raise HTTPException(status_code=400, detail="未能从描述中解析出有效订阅条件，请换一种说法")
    matched = count_matches(db, filters)
    sub = Subscription(
        name=req.name or default_name(req.text),
        sub_type="composite",
        filters_json=json.dumps(filters, ensure_ascii=False),
        auto_add=req.auto_add,
        enabled=True,
        check_interval_hours=req.check_interval_hours,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return {"ok": True, "id": sub.id, "name": sub.name, "filters": filters,
            "engine": engine, "matched_count": matched, "message": f"订阅已创建（当前命中 {matched} 部）"}


@router.post("/from-task")
def create_from_task(req: FromTaskRequest, db: DbSession, _user: CurrentUser):
    """S3: 作品级订阅——按该作品特征（演员/系列/厂牌/标签）追更后续。"""
    from services.ai_subscription import build_filters_from_task
    t = db.get(Task, req.task_id)
    if not t:
        raise HTTPException(status_code=404, detail="作品不存在")
    filters = build_filters_from_task(t)
    if not filters:
        raise HTTPException(status_code=400, detail="该作品没有可用的订阅特征")
    sub = Subscription(
        name=req.name or f"{t.video_code or '作品'}同系列追更",
        sub_type="composite",
        filters_json=json.dumps(filters, ensure_ascii=False),
        auto_add=req.auto_add,
        enabled=True,
        check_interval_hours=req.check_interval_hours,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return {"ok": True, "id": sub.id, "name": sub.name, "filters": filters,
            "message": f"订阅已创建：{sub.name}"}


@router.post("/fill-all-works")
def start_fill_all_works(payload: FillAllWorksRequest, _user: CurrentUser):
    """启动「全部补齐作品」后台任务（串行爬取所有订阅演员的作品）。"""
    from services import actor_works_batch
    ok, msg = actor_works_batch.start(payload.wait_limit_min or 60, payload.max_co_star or 0,
                                      since=payload.since or "")
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

    # 演员订阅：创建后自动首轮补齐——无 URL 时按名搜索源站回写，
    # 爬全部作品并入库新作（URL/作品/新作一次到位）
    if payload.sub_type == "actor" and payload.actor_id:
        import threading

        def _bootstrap_first_crawl():
            try:
                import asyncio as _aio
                from database import SessionLocal as _SL
                from services.new_works_monitor import check_actor_new_works
                ndb = _SL()
                try:
                    _aio.run(check_actor_new_works(
                        payload.actor_id, subscription_id=sub.id,
                        auto_add=bool(payload.auto_add)))
                finally:
                    ndb.close()
            except Exception:
                pass

        threading.Thread(target=_bootstrap_first_crawl, daemon=True).start()

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
