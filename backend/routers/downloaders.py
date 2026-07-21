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
    """读 settings 表，支持 key 别名（前端写 qbittorrent_* 后端读 qb_*）。"""
    # key 别名映射：后端短名 → 前端长名
    aliases = {
        "qb_url": "qbittorrent_url",
        "qb_username": "qbittorrent_username",
        "qb_password": "qbittorrent_password",
        "aria2_url": "aria2_rpc_url",
        "aria2_secret": "aria2_token",
    }
    row = db.get(Setting, key)
    if row and row.value:
        return row.value
    # 尝试别名
    alias = aliases.get(key)
    if alias:
        row = db.get(Setting, alias)
        if row and row.value:
            return row.value
    return ""


def _extract_hash(magnet: str) -> str | None:
    m = re.search(r"btih:([a-fA-F0-9]{40})", magnet)
    return m.group(1).lower() if m else None


class PushRequest(BaseModel):
    magnet: str
    task_id: int | None = None
    downloader: str = "qbittorrent"  # qbittorrent/aria2/transmission


def _push_qbittorrent_sync(magnet: str, config: dict) -> dict:
    """同步函数：推送磁力到 qBittorrent（供 asyncio.to_thread 调用）。

    架构修复：加 REQUESTS_ARGS timeout，防止网络挂起阻塞。
    """
    import qbittorrentapi
    qbc = qbittorrentapi.Client(
        host=config.get("qb_url", ""),
        username=config.get("qb_username", ""),
        password=config.get("qb_password", ""),
        REQUESTS_ARGS={"timeout": 10},
    )
    try:
        qbc.auth_log_in()
        save_path = config.get("qbittorrent_save_path") or None
        result = qbc.torrents_add(urls=magnet, save_path=save_path)
        return {"ok": result == "Ok.", "message": result}
    except Exception as e:
        return {"ok": False, "message": str(e)}
    finally:
        try:
            qbc.auth_log_out()
        except Exception:
            pass


async def _push_qbittorrent(magnet: str, config: dict) -> dict:
    """推送到 qBittorrent（同步 API 包 asyncio.to_thread，不阻塞事件循环）。"""
    import asyncio
    return await asyncio.to_thread(_push_qbittorrent_sync, magnet, config)


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


async def _push_clouddrive(magnet: str, config: dict) -> dict:
    """推送到 CloudDrive2（Connect-RPC JSON API 离线下载）。

    CD2 使用 gRPC over HTTP/2（Connect 协议），但支持 JSON 编码：
    POST /cloud.drive.CloudDrive/CreateOfflineTask
    Content-Type: application/json
    Body: {"magnet_url":"...", "parent_folder_id_or_path":"/path"}
    鉴权：Bearer token（从用户名密码登录获取，或直接配置 API token）
    """
    import httpx
    url = config.get("clouddrive_url", "")
    if not url:
        return {"ok": False, "message": "CloudDrive2 未配置"}
    save_path = config.get("clouddrive_save_path", "/")

    base = url.rstrip("/")
    headers = {"Content-Type": "application/json"}

    # 鉴权：优先用配置的 token；否则用用户名密码登录获取 token
    token = config.get("clouddrive_token", "")
    if not token:
        username = config.get("clouddrive_username", "")
        password = config.get("clouddrive_password", "")
        if username and password:
            try:
                async with httpx.AsyncClient(timeout=15) as c:
                    login_url = base + "/cloud.drive.CloudDrive/GetCaptcha"
                    captcha_resp = await c.post(login_url, json={}, headers={"Content-Type": "application/json"})
                    captcha_id = ""
                    if captcha_resp.status_code == 200:
                        try:
                            captcha_id = captcha_resp.json().get("id", "") or ""
                        except Exception:
                            pass
                    login_url2 = base + "/cloud.drive.CloudDrive/Login"
                    login_payload = {
                        "userName": username,
                        "password": password,
                        "captchaId": captcha_id,
                    }
                    login_resp = await c.post(login_url2, json=login_payload, headers={"Content-Type": "application/json"})
                    if login_resp.status_code == 200:
                        token = login_resp.json().get("token", "")
            except Exception as e:
                return {"ok": False, "message": f"CloudDrive2 登录失败: {e}"}

    if token:
        headers["Authorization"] = f"Bearer {token}"

    # CreateOfflineTask（Connect-RPC JSON 端点）
    api_url = base + "/cloud.drive.CloudDrive/CreateOfflineTask"
    payload = {"magnet_url": magnet, "parent_folder_id_or_path": save_path}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(api_url, json=payload, headers=headers)
            if r.status_code in (200, 201):
                return {"ok": True, "message": "已推送到 CloudDrive2"}
            return {"ok": False, "message": f"CloudDrive2 返回 {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.post("/push")
@router.post("/download")  # 兼容前端旧路径
async def push_magnet(req: PushRequest, db: DbSession, _user: CurrentUser):
    """推送磁力到下载器并记录到 downloads 表。"""
    # 读配置
    config = {}
    for k in ["qb_url", "qb_username", "qb_password", "qbittorrent_save_path",
              "aria2_url", "aria2_secret",
              "clouddrive_url", "clouddrive_token", "clouddrive_save_path",
              "transmission_url", "transmission_username", "transmission_password"]:
        config[k] = _get_setting(db, k)

    # 下载器：空时读 DB 的 default_downloader
    downloader = req.downloader or _get_setting(db, "default_downloader") or "qbittorrent"

    # 推送
    if downloader == "qbittorrent":
        result = await _push_qbittorrent(req.magnet, config)
    elif downloader == "aria2":
        result = await _push_aria2(req.magnet, config)
    elif downloader == "clouddrive":
        result = await _push_clouddrive(req.magnet, config)
    else:
        return {"ok": False, "message": f"暂不支持的下载器: {downloader}"}

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


def _test_qbittorrent_sync(config: dict) -> dict:
    """同步函数：测试 qBittorrent 连接（供 to_thread 调用）。"""
    import qbittorrentapi
    qbc = qbittorrentapi.Client(
        host=config["qb_url"], username=config["qb_username"], password=config["qb_password"],
        REQUESTS_ARGS={"timeout": 10},
    )
    try:
        qbc.auth_log_in()
        version = qbc.app_version()
        qbc.auth_log_out()
        return {"ok": True, "version": version}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.post("/test")
@router.post("/test-connection")  # 兼容前端旧路径
async def test_connection(body: dict, db: DbSession, _user: CurrentUser):
    """测试下载器连接（qB 同步调用包 to_thread，不阻塞事件循环）。

    前端 POST body: {downloader, save_path?}
    """
    import asyncio
    downloader = body.get("downloader", "")
    config = {}
    for k in ["qb_url", "qb_username", "qb_password", "aria2_url", "aria2_secret",
              "clouddrive_url", "clouddrive_token"]:
        config[k] = _get_setting(db, k)
    if downloader == "qbittorrent":
        return await asyncio.to_thread(_test_qbittorrent_sync, config)
    elif downloader == "aria2":
        if not config["aria2_url"]:
            return {"ok": False, "message": "未配置"}
        return {"ok": True, "message": "配置已读取（连接测试需实际推送）"}
    elif downloader == "clouddrive":
        if not config["clouddrive_url"]:
            return {"ok": False, "message": "未配置"}
        # 真实连接测试：调 GetCaptcha 验证服务可达
        import httpx
        base = config["clouddrive_url"].rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(base + "/cloud.drive.CloudDrive/GetCaptcha", json={}, headers={"Content-Type": "application/json"})
                if r.status_code == 200:
                    return {"ok": True, "message": "CloudDrive2 服务可达"}
                return {"ok": False, "message": f"CloudDrive2 返回 {r.status_code}"}
        except Exception as e:
            return {"ok": False, "message": f"连接失败: {e}"}
    return {"ok": False, "message": f"未知下载器: {downloader}"}
