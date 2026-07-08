"""内容过滤路由 —— 规则 CRUD + 应用过滤。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from deps import CurrentUser, DbSession
from models import ContentFilterRule, Task
from services.content_filter import apply_filters, get_rules

router = APIRouter(prefix="/api/filters", tags=["content-filter"])

VALID_ACTIONS = {"hide", "highlight", "blur", "mark"}


class FilterRuleCreate(BaseModel):
    name: str = Field(max_length=100)
    keyword: str = Field(max_length=200)
    is_regex: bool = False
    case_sensitive: bool = False
    action: str = Field(default="hide", max_length=20)
    fields_json: str | None = Field(default=None, max_length=500)
    message: str | None = Field(default=None, max_length=200)
    enabled: bool = True


@router.get("/rules")
def list_rules(db: DbSession, _user: CurrentUser):
    return get_rules(db)


@router.post("/rules", status_code=201)
def create_rule(payload: FilterRuleCreate, db: DbSession, _user: CurrentUser):
    if payload.action not in VALID_ACTIONS:
        raise HTTPException(status_code=400, detail=f"无效动作，可选: {VALID_ACTIONS}")
    rule = ContentFilterRule(**payload.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, payload: FilterRuleCreate, db: DbSession, _user: CurrentUser):
    rule = db.get(ContentFilterRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    for k, v in payload.model_dump().items():
        setattr(rule, k, v)
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, db: DbSession, _user: CurrentUser):
    rule = db.get(ContentFilterRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    db.delete(rule)
    db.commit()
    return {"ok": True}


@router.post("/apply")
def apply_filter_rules(
    db: DbSession,
    _user: CurrentUser,
    list_source_id: int | None = Query(None),
    limit: int = Query(100, le=500),
):
    """对任务列表应用所有规则，返回带过滤标记的结果。"""
    stmt = select(Task).where(Task.status == "visited")
    if list_source_id:
        stmt = stmt.where(Task.list_source_id == list_source_id)
    tasks = db.execute(stmt.limit(limit)).scalars().all()
    return apply_filters(tasks, db)
