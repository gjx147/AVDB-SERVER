"""115 网盘客户端 —— OAuth 设备码扫码 + PKCE + 离线任务管理。

参考 JavdBviewed Drive115V2Service，服务端化：
- OAuth 设备授权码流程（扫码登录）+ PKCE
- token 存 settings 表（drive115_access_token / drive115_refresh_token / drive115_expires_at）
- 离线任务：add_task_urls（磁力推送）/ get_task_list / del_task
- 配额查询：get_quota_info
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time

import httpx
from sqlalchemy import select

from database import SessionLocal
from models import Setting

logger = logging.getLogger("avdb.drive115")

# 115 OpenAPI 端点
_AUTH_BASE = "https://passportapi.115.com"
_API_BASE = "https://proapi.115.com"
_QRCODE_BASE = "https://qrcodeapi.115.com"
_CLIENT_ID = "AVDB-SERVER"  # 115 开放平台 client_id（需实际申请，这里占位）


def _get_setting(db, key: str) -> str:
    row = db.get(Setting, key)
    return row.value if row and row.value else ""


def _set_setting(db, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.commit()


def _get_pkce_pair() -> tuple[str, str]:
    """生成 PKCE code_verifier + code_challenge(S256)。"""
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = hashlib.sha256(verifier.encode()).hexdigest()
    return verifier, challenge


async def init_device_auth() -> dict:
    """发起设备授权，返回扫码信息（供前端展示二维码）。"""
    verifier, challenge = _get_pkce_pair()
    db = SessionLocal()
    try:
        _set_setting(db, "drive115_pkce_verifier", verifier)
    finally:
        db.close()
    payload = {
        "client_id": _CLIENT_ID,
        "code_challenge": challenge,
        "code_challenge_method": "sha256",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{_AUTH_BASE}/open/authDeviceCode", data=payload)
            data = r.json()
            return data
    except Exception as e:
        logger.warning(f"115 设备授权失败: {e}")
        return {"error": str(e)}


async def poll_auth_status(uid: str, sign: str) -> dict:
    """轮询扫码状态。"""
    params = {"uid": uid, "sign": sign, "time": str(int(time.time()))}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{_QRCODE_BASE}/get/status/", params=params)
            return r.json()
    except Exception as e:
        return {"error": str(e)}


async def exchange_token(uid: str) -> dict:
    """扫码确认后，用 uid + PKCE verifier 换取 token。"""
    db = SessionLocal()
    try:
        verifier = _get_setting(db, "drive115_pkce_verifier")
    finally:
        db.close()
    payload = {"uid": uid, "code_verifier": verifier}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{_AUTH_BASE}/open/deviceCodeToToken", data=payload)
            data = r.json()
            if data.get("access_token"):
                db = SessionLocal()
                try:
                    _set_setting(db, "drive115_access_token", data["access_token"])
                    _set_setting(db, "drive115_refresh_token", data.get("refresh_token", ""))
                    expires_at = str(int(time.time()) + data.get("expires_in", 86400))
                    _set_setting(db, "drive115_expires_at", expires_at)
                finally:
                    db.close()
            return data
    except Exception as e:
        return {"error": str(e)}


async def _get_valid_token() -> str | None:
    """获取有效 token（过期则用 refresh_token 刷新）。"""
    db = SessionLocal()
    try:
        token = _get_setting(db, "drive115_access_token")
        refresh = _get_setting(db, "drive115_refresh_token")
        expires = _get_setting(db, "drive115_expires_at")
    finally:
        db.close()
    if not token:
        return None
    # 检查过期
    if expires and int(expires) < int(time.time()) + 60:
        if refresh:
            new_token = await _refresh_token(refresh)
            if new_token:
                return new_token
        return None
    return token


async def _refresh_token(refresh_token: str) -> str | None:
    """刷新 token。"""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{_AUTH_BASE}/open/refreshToken", data={"refresh_token": refresh_token})
            data = r.json()
            if data.get("access_token"):
                db = SessionLocal()
                try:
                    _set_setting(db, "drive115_access_token", data["access_token"])
                    _set_setting(db, "drive115_refresh_token", data.get("refresh_token", refresh_token))
                    _set_setting(db, "drive115_expires_at", str(int(time.time()) + data.get("expires_in", 86400)))
                finally:
                    db.close()
                return data["access_token"]
    except Exception as e:
        logger.warning(f"115 token 刷新失败: {e}")
    return None


async def add_offline_task(magnet: str) -> dict:
    """推送磁力到 115 离线下载。"""
    token = await _get_valid_token()
    if not token:
        return {"ok": False, "message": "115 未授权"}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{_API_BASE}/open/offline/add_task_urls",
                headers={"Authorization": f"Bearer {token}"},
                json={"urls": [{"url": magnet, "file_name": ""}]},
            )
            data = r.json()
            return {"ok": data.get("code") == 0, "data": data}
    except Exception as e:
        return {"ok": False, "message": str(e)}


async def get_task_list() -> dict:
    """查询离线任务列表。"""
    token = await _get_valid_token()
    if not token:
        return {"ok": False, "message": "115 未授权"}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{_API_BASE}/open/offline/get_task_list",
                headers={"Authorization": f"Bearer {token}"},
            )
            return r.json()
    except Exception as e:
        return {"error": str(e)}


async def get_quota() -> dict:
    """查询离线配额。"""
    token = await _get_valid_token()
    if not token:
        return {"ok": False, "message": "115 未授权"}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{_API_BASE}/open/offline/get_quota_info",
                headers={"Authorization": f"Bearer {token}"},
            )
            return r.json()
    except Exception as e:
        return {"error": str(e)}
