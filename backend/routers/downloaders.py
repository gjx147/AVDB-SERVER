"""下载器路由 —— 推送磁力到 qBittorrent / aria2 / transmission。

配置存 settings 表：
- qb_url / qb_username / qb_password
- aria2_url / aria2_secret
- transmission_url / transmission_username / transmission_password
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from database import SessionLocal
from deps import CurrentUser, DbSession
from models import Download, Setting, Task

logger = logging.getLogger("avdb.downloaders")

router = APIRouter(prefix="/api/downloaders", tags=["downloaders"])


def _get_setting(db, key: str) -> str:
    row = db.get(Setting, key)
    return row.value if row and row.value else ""


def _extract_hash(magnet: str) -> str | None:
    m = re.search(r"btih:([a-fA-F0-9]{40})", magnet)
    return m.group(1).lower() if m else None


class PushRequest(BaseModel):
    magnet: str
    task_id: int | None = None
    downloader: str = "qbittorrent"  # qbittorrent/aria2/transmission


async def _push_qbittorrent(magnet: str, config: dict) -> dict:
    """推送到 qBittorrent。"""
    import qbittorrentapi
    qbc = qbittorrentapi.Client(
        host=config.get("qb_url", ""),
        username=config.get("qb_username", ""),
        password=config.get("qb_password", ""),
    )
    try:
        qbc.auth_log_in()
        result = qbc.torrents_add(urls=magnet)
        return {"ok": result == "Ok.", "message": result}
    except Exception as e:
        return {"ok": False, "message": str(e)}
    finally:
        try:
            qbc.auth_log_out()
        except Exception:
            pass


async def _push_aria2(magnet: str, config: dict) -> dict:
    """推送到 aria2（JSON-RPC）。"""
    import httpx
    import json
    url = config.get("aria2_url", "")
    secret = config.get("aria2_secret", "")
    if not url:
        return {"ok": False, "message": "aria2 未配置"}
    payload = {
        "jsonrpc": "2.0", "id": "1", "method": "aria2.addUri",
        "params": [[magnet]] + ([f"token:{secret}"] if secret else []),
    }
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, json=payload)
            data = r.json()
            if "result" in data:
                return {"ok": True, "gid": data["result"]}
            return {"ok": False, "message": data.get("error", {}).get("message", "未知错误")}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.post("/push")
async def push_magnet(req: PushRequest, db: DbSession, _user: CurrentUser):
    """推送磁力到下载器并记录到 downloads 表。"""
    # 读配置
    config = {}
    for k in ["qb_url", "qb_username", "qb_password", "aria2_url", "aria2_secret",
              "transmission_url", "transmission_username", "transmission_password"]:
        config[k] = _get_setting(db, k)

    # 推送
    if req.downloader == "qbittorrent":
        result = await _push_qbittorrent(req.magnet, config)
    elif req.downloader == "aria2":
        result = await _push_aria2(req.magnet, config)
    else:
        return {"ok": False, "message": f"暂不支持的下载器: {req.downloader}"}

    # 记录到 downloads 表
    task = db.get(Task, req.task_id) if req.task_id else None
    dl = Download(
        task_id=req.task_id,
        video_code=task.video_code if task else None,
        magnet=req.magnet,
        info_hash=_extract_hash(req.magnet),
        downloader=req.downloader,
        status="pushed" if result["ok"] else "failed",
        error_message=None if result["ok"] else result.get("message"),
    )
    db.add(dl)
    db.commit()
    db.refresh(dl)
    return {"ok": result["ok"], "download_id": dl.id, "message": result.get("message")}


@router.get("/test")
async def test_connection(downloader: str, db: DbSession, _user: CurrentUser):
    """测试下载器连接。"""
    config = {}
    for k in ["qb_url", "qb_username", "qb_password", "aria2_url", "aria2_secret"]:
        config[k] = _get_setting(db, k)
    if downloader == "qbittorrent":
        try:
            import qbittorrentapi
            qbc = qbittorrentapi.Client(
                host=config["qb_url"], username=config["qb_username"], password=config["qb_password"])
            qbc.auth_log_in()
            version = qbc.app_version()
            qbc.auth_log_out()
            return {"ok": True, "version": version}
        except Exception as e:
            return {"ok": False, "message": str(e)}
    elif downloader == "aria2":
        if not config["aria2_url"]:
            return {"ok": False, "message": "未配置"}
        return {"ok": True, "message": "配置已读取（连接测试需实际推送）"}
    return {"ok": False, "message": f"未知下载器: {downloader}"}
