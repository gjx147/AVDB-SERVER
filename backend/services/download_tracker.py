"""下载进度追踪服务 —— 轮询 qBittorrent 状态，回写 downloads 表。

AVDB download_tracker 的去补丁化重写：
- 改用 ORM（SessionLocal）替代直接 sqlite3
- async（挂 APScheduler）
- 完成时触发通知
- 结构设计支持扩展 aria2/transmission（轮询各自 API）
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from database import SessionLocal
from models import Download, Setting

logger = logging.getLogger("avdb.download_tracker")

_state = {"running": False, "last_run": None, "updated": 0}

# qBittorrent 状态映射
_QB_COMPLETED = {"uploading", "queuedUP", "stalledUP", "forcedUP", "pausedUP", "checkingUP"}
_QB_DOWNLOADING = {"downloading", "metaDL", "forcedDL", "queuedDL", "stalledDL", "checkingDL"}
_QB_FAILED = {"missingFiles", "error"}


def _get_setting(db, key: str) -> str:
    row = db.get(Setting, key)
    return row.value if row and row.value else ""


async def _poll_qbittorrent(db) -> int:
    """轮询 qBittorrent，更新所有 pushed/downloading 的 qB 下载记录。返回更新数。"""
    import qbittorrentapi

    config = {k: _get_setting(db, k) for k in ["qb_url", "qb_username", "qb_password"]}
    if not config["qb_url"]:
        return 0

    # 取所有需要追踪的 qB 下载记录
    pending = db.execute(
        select(Download).where(
            Download.downloader == "qbittorrent",
            Download.status.in_(["pushed", "downloading"]),
        )
    ).scalars().all()
    if not pending:
        return 0

    qbc = qbittorrentapi.Client(
        host=config["qb_url"], username=config["qb_username"], password=config["qb_password"]
    )
    updated = 0
    try:
        qbc.auth_log_in()
        torrents = {t.infohash_v1.lower(): t for t in qbc.torrents_info() if t.infohash_v1}
        for dl in pending:
            if not dl.info_hash:
                continue
            t = torrents.get(dl.info_hash)
            if not t:
                continue
            state = str(t.state)
            progress = round(float(t.progress) * 100, 1) if t.progress else 0
            dl.progress = progress
            if state in _QB_COMPLETED:
                dl.status = "completed"
                dl.completed_at = datetime.utcnow()
                updated += 1
            elif state in _QB_DOWNLOADING:
                dl.status = "downloading"
                updated += 1
            elif state in _QB_FAILED:
                dl.status = "failed"
                dl.error_message = f"qBittorrent state: {state}"
                updated += 1
        db.commit()
    except Exception as e:
        logger.warning(f"qBittorrent 轮询失败: {e}")
    finally:
        try:
            qbc.auth_log_out()
        except Exception:
            pass
    return updated


async def run_track_cycle() -> dict:
    """执行一轮下载进度轮询。"""
    if _state["running"]:
        return {"ok": False, "message": "已在运行"}
    _state["running"] = True
    try:
        db = SessionLocal()
        try:
            updated = await _poll_qbittorrent(db)
        finally:
            db.close()
        _state["last_run"] = datetime.utcnow().isoformat()
        _state["updated"] = updated

        # 通知新完成的
        if updated > 0:
            try:
                from services.notifier import notify
                await notify("download", "下载进度更新", f"{updated} 个任务状态已更新")
            except Exception:
                pass

        return {"ok": True, "updated": updated}
    finally:
        _state["running"] = False


def get_state() -> dict:
    return dict(_state)


def register_job(interval: int = 60) -> None:
    """注册到调度中心（默认每 60 秒轮询）。"""
    from services.scheduler import add_interval_job

    add_interval_job(run_track_cycle, "download-tracker", seconds=interval)
    logger.info("download_tracker 已注册: 每 %ds", interval)
