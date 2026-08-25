"""自动整理路由（F7）：配置读写 + 手动全量 + 解除整理。"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
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


@router.post("/subtitle/{dl_id}")
async def upload_subtitle(dl_id: int, db: DbSession, _user: CurrentAdmin, file: UploadFile = File(...)):
    """N26: 为已整理的任务上传字幕文件（.srt/.ass），复制到媒体文件旁。

    路径规则：媒体文件同目录同名（.mp4 → .srt）；多文件取第一个。
    """
    from pathlib import Path as _Path

    from models import Download

    if not file.filename or not file.filename.lower().endswith((".srt", ".ass")):
        raise HTTPException(status_code=400, detail="仅支持 .srt / .ass 字幕文件")
    dl = db.get(Download, dl_id)
    if not dl or not dl.organized_path:
        raise HTTPException(status_code=404, detail="该下载记录未整理（无媒体文件路径）")
    media_paths = [p for p in dl.organized_path.split(";") if p]
    if not media_paths:
        raise HTTPException(status_code=404, detail="无媒体文件路径")
    video = _Path(media_paths[0])
    if not video.exists():
        raise HTTPException(status_code=404, detail=f"媒体文件不存在: {video}")
    sub_path = video.with_suffix(file.filename[file.filename.rfind("."):])
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="字幕文件过大（>5MB）")
    sub_path.write_bytes(content)
    return {"ok": True, "path": str(sub_path), "name": sub_path.name}
