"""下载进度追踪服务 —— 轮询 qBittorrent 状态，回写 downloads 表。

AVDB download_tracker 的去补丁化重写：
- 改用 ORM（SessionLocal）替代直接 sqlite3
- async（挂 APScheduler）
- 完成时触发通知
- 结构设计支持扩展 aria2/transmission（轮询各自 API）
"""

from __future__ import annotations

import asyncio
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


def _poll_qbittorrent_sync(config: dict, hashes: list[tuple[int, str]]) -> list[dict]:
    """同步函数：连接 qBittorrent 查询状态（供 asyncio.to_thread 调用）。

    hashes: [(download_id, info_hash), ...]
    返回: [{id, status, progress, error}, ...]
    """
    import qbittorrentapi

    results: list[dict] = []
    qbc = qbittorrentapi.Client(
        host=config["qb_url"],
        username=config["qb_username"],
        password=config["qb_password"],
        REQUESTS_ARGS={"timeout": 10},  # 10s 超时，防止网络挂起
    )
    try:
        qbc.auth_log_in()
        torrents = {t.infohash_v1.lower(): t for t in qbc.torrents_info() if t.infohash_v1}
        for dl_id, info_hash in hashes:
            t = torrents.get(info_hash)
            if not t:
                continue
            state = str(t.state)
            progress = round(float(t.progress) * 100, 1) if t.progress else 0
            if state in _QB_COMPLETED:
                results.append({"id": dl_id, "status": "completed", "progress": progress, "error": None})
            elif state in _QB_DOWNLOADING:
                results.append({"id": dl_id, "status": "downloading", "progress": progress, "error": None})
            elif state in _QB_FAILED:
                results.append({"id": dl_id, "status": "failed", "progress": progress, "error": f"qB state: {state}"})
    finally:
        try:
            qbc.auth_log_out()
        except Exception:
            pass
    return results


async def _poll_qbittorrent(db) -> int:
    """轮询 qBittorrent，更新所有 pushed/downloading 的 qB 下载记录。返回更新数。

    架构修复：同步 qB API 调用包 asyncio.to_thread，不阻塞事件循环。
    """
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

    hashes = [(dl.id, dl.info_hash) for dl in pending if dl.info_hash]
    if not hashes:
        return 0

    # 关键修复：同步调用放线程池，不阻塞事件循环
    try:
        results = await asyncio.to_thread(_poll_qbittorrent_sync, config, hashes)
    except Exception as e:
        logger.warning(f"qBittorrent 轮询失败: {e}")
        return 0

    # 回写 DB
    updated = 0
    dl_map = {dl.id: dl for dl in pending}
    for r in results:
        dl = dl_map.get(r["id"])
        if not dl:
            continue
        dl.progress = r["progress"]
        dl.status = r["status"]
        dl.error_message = r["error"]
        if r["status"] == "completed":
            dl.completed_at = datetime.utcnow()
        updated += 1
    if updated:
        db.commit()
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
