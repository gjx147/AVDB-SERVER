"""列表源管理路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from deps import CurrentUser, DbSession
from models import ListSource
from schemas import ListSourceCreate, ListSourceOut

router = APIRouter(prefix="/api/list-sources", tags=["list-sources"])


@router.get("", response_model=list[ListSourceOut])
def list_sources(db: DbSession, _user: CurrentUser):
    return db.execute(select(ListSource).order_by(ListSource.id)).scalars().all()


@router.post("", response_model=ListSourceOut, status_code=201)
def create_source(payload: ListSourceCreate, db: DbSession, _user: CurrentUser):
    code = payload.list_code.strip().upper()
    existing = db.execute(select(ListSource).where(ListSource.list_code == code)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"列表源 {code} 已存在")
    src = ListSource(
        list_code=code,
        list_path=payload.list_path or f"/video_codes/{code}",
        list_params=payload.list_params,
        max_pages=payload.max_pages,
    )
    db.add(src)
    db.commit()
    db.refresh(src)
    return src


@router.delete("/{source_id}")
def delete_source(source_id: int, db: DbSession, _user: CurrentUser):
    src = db.get(ListSource, source_id)
    if not src:
        raise HTTPException(status_code=404, detail="列表源不存在")
    db.delete(src)
    db.commit()
    return {"ok": True, "message": "已删除"}
