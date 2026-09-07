"""下载完成自动整理（F7）：硬链接 + Emby 规范命名。

流程：qB 下载完成 → 定位文件 → 番号识别 → 查库匹配 → 规范命名 →
硬链接进媒体库目录（跨文件系统降级复制）→ 记录整理标记与通知历史。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from sqlalchemy import select

from database import SessionLocal
from models import Download, Setting, Task
from services.settings_util import get_setting as _get_setting

logger = logging.getLogger("avdb.organizer")

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".ts", ".m2ts", ".webm"}
_CODE_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{2,5}-\d{2,5}[A-Z0-9]?)(?![A-Z0-9])")


def _extract_code(filename: str) -> str | None:
    m = _CODE_RE.search(filename.upper())
    return m.group(1) if m else None


def _build_name(template: str, code: str, title: str, ext: str) -> str:
    name = template.replace("{code}", code).replace("{title}", (title or code).strip() or code)
    return f"{name}{ext}"


def _link_or_copy(src: Path, dst: Path) -> None:
    """硬链接优先；跨文件系统 OSError 时降级为复制。"""
    try:
        os.link(src, dst)
    except OSError:
        import shutil
        shutil.copy2(src, dst)


async def run_organize_all() -> dict:
    """手动全量整理：所有 completed 且未整理的下载记录（qB 已退役，仅 CD2 路径）。"""
    db = SessionLocal()
    try:
        rows = db.execute(
            select(Download).where(
                Download.downloader == "clouddrive",
                Download.status == "completed",
                Download.organized == False,  # noqa: E712
            )
        ).scalars().all()
        ok_count = 0
        results = []
        cd_config = {k: _get_setting(db, k) for k in (
            "clouddrive_save_path",)}
        for dl in rows:
            if dl.downloader == "clouddrive":
                # CD2 原地整理（≥200MB 重命名番号，杂文件删除）——统一走 cd2_rename 模块
                try:
                    from services.cd2_rename import run_rename_now
                    ok, msg2 = await asyncio.to_thread(run_rename_now, dl.task_id, dl.video_code)
                except Exception as e:
                    ok, msg2 = False, f"CD2 整理异常: {e}"
                if ok:
                    ok_count += 1
                results.append({"dl_id": dl.id, "ok": ok, "message": msg2})
                continue
            results.append({"dl_id": dl.id, "ok": False, "message": "不支持的下载器（qB 已退役，仅 CD2 支持整理）"})
        return {"ok": True, "total": len(rows), "organized": ok_count, "results": results}
    finally:
        db.close()


def undo_organize(dl_id: int) -> dict:
    """解除整理：删除媒体库侧链接，不影响下载侧文件。"""
    db = SessionLocal()
    try:
        dl = db.get(Download, dl_id)
        if not dl or not dl.organized_path:
            return {"ok": False, "message": "该记录未整理"}
        removed = 0
        for p in (dl.organized_path or "").split(";"):
            try:
                if p and Path(p).exists():
                    Path(p).unlink()
                    removed += 1
            except Exception:
                pass
        dl.organized = False
        dl.organized_path = None
        db.commit()
        return {"ok": True, "removed": removed}
    finally:
        db.close()
