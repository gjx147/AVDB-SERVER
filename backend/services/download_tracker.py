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
from models import Download, Setting, Task
from services.settings_util import get_setting as _get_setting

logger = logging.getLogger("avdb.download_tracker")

_state = {"running": False, "last_run": None, "updated": 0}

# T4: torrent 从 qB 消失的连续未命中计数（dl_id → 轮次），3 轮未命中标记失败
_missing_count: dict[int, int] = {}

# 迅雷状态映射（phase → 内部状态）
_XL_COMPLETE = {"PHASE_TYPE_COMPLETE"}
_XL_ERROR = {"PHASE_TYPE_ERROR"}


def _poll_xunlei_sync(config: dict) -> list[dict]:
    """同步拉取迅雷运行中任务列表。返回 [{name, phase, progress, speed, real_path, status}]。"""
    if (config.get("xunlei_push_channel") or "container").lower() == "mcp":
        return _poll_xunlei_mcp(config)
    from services.xunlei_client import XunleiClient
    client = XunleiClient(config.get("xunlei_url", ""),
                          config.get("xunlei_basic_user", ""),
                          config.get("xunlei_basic_pass", ""))
    out = []
    for t in client.list_tasks("active"):
        p = t.get("params") or {}
        out.append({
            "name": t.get("name", ""),
            "phase": t.get("phase", ""),
            "progress": int(t.get("progress") or 0),
            "speed": int(p.get("speed") or 0),
            "real_path": p.get("real_path", ""),
            "status": str(p.get("status") or t.get("message") or ""),
        })
    return out


def _poll_xunlei_mcp(config: dict) -> list[dict]:
    """MCP 通道轮询：list_devices → 各设备任务列表 → 统一格式（phase 数字兼容）。"""
    import asyncio
    from services.xunlei_mcp import XunleiMCPClient
    url = config.get("xunlei_mcp_url", "")
    if not url or url == "***":
        return []

    async def _run():
        async with XunleiMCPClient(url, timeout=15.0) as c:
            devices = await c.list_devices()
            out = []
            for dev in devices:
                for t in await c.list_tasks_mcp(dev.get("target", ""), 200):
                    out.append({
                        "name": t.get("name") or t.get("file_name") or "",
                        "phase": t.get("phase", ""),
                        "progress": int(t.get("progress") or 0),
                        "speed": int(t.get("speed") or 0),
                        "real_path": t.get("file_name", ""),
                        "status": str(t.get("status") or t.get("message") or ""),
                    })
            return out

    try:
        return asyncio.run(_run())
    except Exception as e:
        logger.warning(f"迅雷 MCP 轮询失败: {e}")
        return []


async def _poll_xunlei(db) -> int:
    """轮询迅雷任务并回写 Download/Task（匹配键 = Download.video_code 与迅雷任务名）。"""
    config = {k: _get_setting(db, k) for k in ["xunlei_url", "xunlei_basic_user", "xunlei_basic_pass",
                                                 "xunlei_mcp_url", "xunlei_push_channel"]}
    channel = (config.get("xunlei_push_channel") or "container").lower()
    if channel == "mcp":
        if not config.get("xunlei_mcp_url") or config.get("xunlei_mcp_url") == "***":
            return 0
    elif not config.get("xunlei_url"):
        return 0
    pending = db.execute(
        select(Download).where(
            Download.downloader == "xunlei",
            Download.status.in_(["pushed", "downloading"]),
        )
    ).scalars().all()
    if not pending:
        return 0
    try:
        results = await asyncio.to_thread(_poll_xunlei_sync, config)
    except Exception as e:
        logger.warning(f"迅雷轮询失败: {e}")
        return 0
    by_name = {r["name"]: r for r in results if r["name"]}
    updated = 0
    for dl in pending:
        r = by_name.get(dl.video_code)
        if not r:
            # A3：未匹配轮次计数，连续 3 轮未命中判失败（恢复 qB 时代的兜底）
            _missing_count[dl.id] = _missing_count.get(dl.id, 0) + 1
            if _missing_count[dl.id] < 3:
                continue
            dl.status = "failed"
            dl.error_message = "迅雷任务未找到（可能已在容器中被删除）"
            _missing_count.pop(dl.id, None)
        else:
            _missing_count.pop(dl.id, None)
            if r["phase"] in _XL_COMPLETE or r["phase"] == 4 or r["progress"] >= 100:
                dl.status = "completed"
                dl.progress = 100
                dl.completed_at = datetime.utcnow()
            elif r["phase"] in _XL_ERROR or r["phase"] == 5 or "失败" in r.get("status", ""):
                dl.status = "failed"
                dl.error_message = "迅雷任务错误"
            else:
                dl.status = "downloading"
                dl.progress = r["progress"]
        # 同步 Task.download_status（接通前端状态横幅/海报角标）
        if dl.task_id:
            _t = db.get(Task, dl.task_id)
            if _t:
                _t.download_status = dl.status
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
            updated = await _poll_xunlei(db)
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
