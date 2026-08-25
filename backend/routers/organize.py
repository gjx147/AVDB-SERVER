"""自动整理路由（F7）：配置读写 + 手动全量 + 解除整理。"""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from deps import CurrentAdmin, CurrentUser, DbSession
from models import Setting

router = APIRouter(prefix="/api/organize", tags=["organize"])

_CONFIG_KEYS = ("organize_enabled", "organize_target_dir", "organize_naming", "organize_keep_source")


@router.get("/config")
def get_config(db: DbSession, _user: CurrentUser):
    def _g(k: str) -> str:
        row = db.get(Setting, k)
        return row.value if row else ""
    return {k: _g(k) for k in _CONFIG_KEYS}


@router.put("/config")
def set_config(payload: dict, db: DbSession, _user: CurrentAdmin):
    for key in _CONFIG_KEYS:
        if key not in payload:
            continue
        val = str(payload[key])
        row = db.get(Setting, key)
        if row:
            row.value = val
        else:
            db.add(Setting(key=key, value=val))
    db.commit()
    return {"ok": True}


@router.post("/run-all")
async def run_all(_user: CurrentAdmin):
    from services.organizer import run_organize_all
    return await run_organize_all()


@router.post("/undo/{dl_id}")
def undo(dl_id: int, _user: CurrentAdmin):
    from services.organizer import undo_organize
    return undo_organize(dl_id)
