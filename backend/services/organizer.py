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

logger = logging.getLogger("avdb.organizer")

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".ts", ".m2ts", ".webm"}
_CODE_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{2,5}-\d{2,5}[A-Z0-9]?)(?![A-Z0-9])")


def _get_setting(db, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row and row.value else default


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


def _organize_one(db, dl: Download, qb_config: dict, target_dir: Path, template: str) -> tuple[bool, str]:
    """整理单个下载记录（同步，跑在线程池）。返回 (ok, message)。"""
    import qbittorrentapi

    qbc = qbittorrentapi.Client(
        host=qb_config.get("qb_url", ""),
        username=qb_config.get("qb_username", ""),
        password=qb_config.get("qb_password", ""),
        REQUESTS_ARGS={"timeout": 10},
    )
    try:
        qbc.auth_log_in()
        torrents = {t.infohash_v1.lower(): t for t in qbc.torrents_info() if t.infohash_v1}
        t = torrents.get((dl.info_hash or "").lower())
        if not t:
            return False, "qB 中找不到对应 torrent"
        save_path = str(getattr(t, "save_path", "") or "")
        if not save_path:
            return False, "torrent 无保存路径"
        try:
            files = qbc.torrents_files(t.hash)
        except Exception:
            files = []
        video_files = [f for f in (files or []) if Path(str(getattr(f, "name", ""))).suffix.lower() in VIDEO_EXTS]
        if not video_files:
            return False, "未找到视频文件"
        if not target_dir.exists():
            return False, f"整理目录不存在: {target_dir}"
        organized_any = 0
        organized_paths: list[str] = []
        for f in video_files:
            fname = str(getattr(f, "name", ""))
            if not fname:
                continue
            code = _extract_code(Path(fname).stem)
            if not code:
                continue
            task = db.execute(select(Task).where(Task.video_code == code)).scalars().first()
            title = task.title if task else ""
            dst = target_dir / _build_name(template, code, title, Path(fname).suffix)
            src = Path(save_path) / fname
            if not src.exists():
                continue
            if dst.exists():
                organized_paths.append(str(dst))  # 幂等：已存在视为已整理
                organized_any += 1
                continue
            _link_or_copy(src, dst)
            organized_paths.append(str(dst))
            organized_any += 1
        if organized_any:
            dl.organized = True
            dl.organized_path = ";".join(organized_paths)
            db.commit()
            return True, f"已整理 {organized_any} 个文件 → {target_dir}"
        return False, "未能识别番号（跳过）"
    finally:
        try:
            qbc.auth_log_out()
        except Exception:
            pass


async def trigger_organize(dl_id: int, info_hash: str | None = None) -> dict:
    """下载完成后的异步整理触发（F7）。返回 {ok, message}。"""
    db = SessionLocal()
    try:
        dl = db.get(Download, dl_id)
        if not dl or dl.downloader != "qbittorrent":
            return {"ok": False, "message": "仅支持 qBittorrent 本地下载"}
        if _get_setting(db, "organize_enabled") != "true":
            return {"ok": False, "message": "自动整理未启用"}
        target = _get_setting(db, "organize_target_dir")
        if not target:
            return {"ok": False, "message": "未配置整理目录"}
        qb_config = {k: _get_setting(db, k) for k in ("qb_url", "qb_username", "qb_password")}
        template = _get_setting(db, "organize_naming") or "{code} - {title}"
        # 打磨：qB 登录/查询等异常兜底，避免后台任务静默失败
        try:
            ok, msg = await asyncio.to_thread(_organize_one, db, dl, qb_config, Path(target), template)
        except Exception as e:
            logger.error("整理异常 dl_id=%s: %s", dl_id, e)
            ok, msg = False, f"整理异常: {str(e)[:200]}"
        try:
            from models import NotifyLog
            db.add(NotifyLog(
                event="organize", title=f"整理 {dl.video_code or dl_id}",
                body=msg[:2000], channel="organizer", ok=ok,
                message="" if ok else msg[:500],
            ))
            db.commit()
        except Exception:
            pass
        return {"ok": ok, "message": msg}
    finally:
        db.close()


async def run_organize_all() -> dict:
    """手动全量整理：所有 completed 且未整理的 qB 记录。"""
    db = SessionLocal()
    try:
        rows = db.execute(
            select(Download).where(
                Download.downloader == "qbittorrent",
                Download.status == "completed",
                Download.organized == False,  # noqa: E712
            )
        ).scalars().all()
        ok_count = 0
        results = []
        for dl in rows:
            r = await trigger_organize(dl.id, dl.info_hash)
            if r.get("ok"):
                ok_count += 1
            results.append({"dl_id": dl.id, **r})
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
