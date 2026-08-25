"""自动化规则路由（N22）：规则 CRUD + 立即运行。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from deps import CurrentAdmin, DbSession
from models import Rule

router = APIRouter(prefix="/api/rules", tags=["rules"])


@router.get("")
def list_rules(db: DbSession, _user: CurrentAdmin):
    rows = db.execute(select(Rule).order_by(Rule.id.desc())).scalars().all()
    return {"ok": True, "items": [
        {"id": r.id, "name": r.name, "conditions_json": r.conditions_json,
         "actions_json": r.actions_json, "enabled": r.enabled,
         "hit_count": r.hit_count, "last_run_at": str(r.last_run_at) if r.last_run_at else None}
        for r in rows
    ]}


@router.post("")
def create_rule(payload: dict, db: DbSession, _user: CurrentAdmin):
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="规则名必填")
    import json
    try:
        cond = json.dumps(payload.get("conditions") or {}, ensure_ascii=False)
        acts = json.dumps(payload.get("actions") or {}, ensure_ascii=False)
    except Exception:
        raise HTTPException(status_code=400, detail="条件/动作格式错误")
    r = Rule(name=name[:100], conditions_json=cond, actions_json=acts,
             enabled=bool(payload.get("enabled", True)))
    db.add(r)
    db.commit()
    return {"ok": True, "id": r.id}


@router.put("/{rule_id}")
def update_rule(rule_id: int, payload: dict, db: DbSession, _user: CurrentAdmin):
    r = db.get(Rule, rule_id)
    if not r:
        raise HTTPException(status_code=404, detail="规则不存在")
    import json
    if "name" in payload and str(payload["name"]).strip():
        r.name = str(payload["name"]).strip()[:100]
    if "conditions" in payload:
        r.conditions_json = json.dumps(payload["conditions"], ensure_ascii=False)
    if "actions" in payload:
        r.actions_json = json.dumps(payload["actions"], ensure_ascii=False)
    if "enabled" in payload:
        r.enabled = bool(payload["enabled"])
    db.commit()
    return {"ok": True}


@router.delete("/{rule_id}")
def delete_rule(rule_id: int, db: DbSession, _user: CurrentAdmin):
    r = db.get(Rule, rule_id)
    if not r:
        raise HTTPException(status_code=404, detail="规则不存在")
    db.delete(r)
    db.commit()
    return {"ok": True}


@router.post("/run-now")
async def run_now(_user: CurrentAdmin):
    from services.rule_engine import evaluate_all
    return await evaluate_all()
