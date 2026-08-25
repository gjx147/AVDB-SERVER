"""公开分享（N21）：收藏夹/演员/榜单只读分享链接。"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from deps import CurrentAdmin, CurrentUser, DbSession
from models import ShareToken, Task, task_collections

router = APIRouter(prefix="/api/shares", tags=["shares"])


@router.post("")
def create_share(payload: dict, db: DbSession, _user: CurrentUser):
    """生成分享链接：{kind: collection|actor|ranking, ref_id, note?, days?}。"""
    kind = payload.get("kind", "")
    ref_id = payload.get("ref_id")
    if kind not in ("collection", "actor", "ranking") or not isinstance(ref_id, int):
        raise HTTPException(status_code=400, detail="kind 仅支持 collection/actor/ranking，ref_id 必填")
    days = int(payload.get("days") or 7)
    if days < 1 or days > 30:
        days = 7
    token = secrets.token_urlsafe(16)[:24]
    st = ShareToken(
        token=token, kind=kind, ref_id=ref_id,
        note=str(payload.get("note") or "")[:200],
        expires_at=datetime.utcnow() + timedelta(days=days),
    )
    db.add(st)
    db.commit()
    return {"ok": True, "token": token, "kind": kind, "ref_id": ref_id,
            "expires_at": str(st.expires_at), "url": f"/share/{token}"}


@router.get("")
def list_shares(db: DbSession, _user: CurrentUser):
    rows = db.execute(select(ShareToken).order_by(ShareToken.id.desc()).limit(50)).scalars().all()
    return {"ok": True, "items": [
        {"token": s.token, "kind": s.kind, "ref_id": s.ref_id, "note": s.note,
         "expires_at": str(s.expires_at), "url": f"/share/{s.token}"}
        for s in rows
    ]}


@router.delete("/{token}")
def delete_share(token: str, db: DbSession, _user: CurrentUser):
    row = db.execute(select(ShareToken).where(ShareToken.token == token)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="分享不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}
