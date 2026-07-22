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
        # qBittorrent torrents_add 返回 "Ok." 或 "Fails."，但不同版本/已存在任务时
        # 返回值可能不同。只要没抛异常且返回值不明确含 "Fail" 就视为成功。
        result_str = str(result).strip()
        ok = "fail" not in result_str.lower()
        return {"ok": ok, "message": result_str or "已添加"}
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


def _cd2_encode_string(field_num: int, value: str) -> bytes:
    """编码 protobuf string 字段（wire_type=2, length-delimited）。

    格式: tag_byte + varint_length + utf8_bytes
    tag = (field_number << 3) | 2
    """
    tag = (field_num << 3) | 2
    data = value.encode("utf-8")
    # varint 编码长度（简单情况：< 128 单字节）
    length_bytes = []
    v = len(data)
    while v > 0:
        length_bytes.append(v & 0x7F)
        v >>= 7
    if not length_bytes:
        length_bytes = [0]
    length_bytes = bytes([(b | 0x80) for b in length_bytes[:-1]] + [length_bytes[-1]])
    return bytes([tag]) + length_bytes + data


def _cd2_encode_varint(field_num: int, value: int) -> bytes:
    """编码 protobuf varint 字段（wire_type=0，用于 bool/int）。"""
    tag = (field_num << 3) | 0
    v = value
    out = []
    while v > 0:
        out.append(v & 0x7F)
        v >>= 7
    if not out:
        out = [0]
    return bytes([tag]) + bytes([(b | 0x80) for b in out[:-1]] + [out[-1]])


def _cd2_grpc_web_frame(payload: bytes) -> bytes:
    """构造 gRPC-Web 帧：flag(1b, 0) + length(4b BE) + payload。"""
    return b"\x00" + len(payload).to_bytes(4, "big") + payload


def _cd2_parse_grpc_web_response(body: bytes) -> tuple[bytes, str]:
    """解析 gRPC-Web 响应。返回 (data_payload, grpc_status)。

    响应由多个帧组成：
    - 数据帧: flag=0x00, 含 protobuf payload
    - trailer 帧: flag=0x80, 含 grpc-status:N 文本
    """
    data = b""
    grpc_status = ""
    i = 0
    while i + 5 <= len(body):
        flag = body[i]
        length = int.from_bytes(body[i + 1:i + 5], "big")
        if i + 5 + length > len(body):
            break
        chunk = body[i + 5:i + 5 + length]
        if flag & 0x80:
            # trailer 帧
            text = chunk.decode("utf-8", errors="replace")
            for line in text.split("\r\n"):
                if line.startswith("grpc-status:"):
                    grpc_status = line.split(":", 1)[1].strip()
        else:
            data += chunk
        i += 5 + length
    return data, grpc_status


async def _cd2_grpc_web_call(base: str, method: str, payload: bytes, token: str = "") -> tuple[bytes, str, int]:
    """调用 CD2 gRPC-Web 端点。返回 (data, grpc_status, http_status)。"""
    import httpx
    frame = _cd2_grpc_web_frame(payload)
    headers = {"Content-Type": "application/grpc-web+proto", "X-Grpc-Web": "1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = base.rstrip("/") + "/clouddrive.CloudDriveFileSrv/" + method
    async with httpx.AsyncClient(timeout=15, verify=False) as c:
        r = await c.post(url, content=frame, headers=headers)
        data, grpc_status = _cd2_parse_grpc_web_response(r.content)
        # grpc-status 可能也在响应头
        if not grpc_status:
            grpc_status = r.headers.get("grpc-status", "")
        return data, grpc_status, r.status_code


async def _push_clouddrive(magnet: str, config: dict) -> dict:
    """推送到 CloudDrive2（gRPC-Web 协议）。

    CD2 用纯 gRPC（非 REST），web UI 通过 gRPC-Web 网关调用。
    端点：POST /clouddrive.CloudDriveFileSrv/CreateOfflineTask
    Content-Type: application/grpc-web+proto
    鉴权：Bearer token（从 GetToken 获取，或直接配置 API token）
    """
    import logging
    logger = logging.getLogger("avdb.downloaders.cd2")
    url = config.get("clouddrive_url", "")
    if not url:
        return {"ok": False, "message": "CloudDrive2 未配置"}
    save_path = config.get("clouddrive_save_path", "/")

    # 鉴权：优先 token；否则用户名密码 GetToken
    token = config.get("clouddrive_token", "")
    if not token:
        username = config.get("clouddrive_username", "")
        password = config.get("clouddrive_password", "")
        if username and password:
            try:
                logger.info(f"CD2 登录: 用户名={username}")
                # GetTokenRequest: field1=userName, field2=password
                login_payload = _cd2_encode_string(1, username) + _cd2_encode_string(2, password)
                data, gstatus, httpstatus = await _cd2_grpc_web_call(url, "GetToken", login_payload)
                logger.info(f"CD2 登录响应: grpc-status={gstatus}, data_len={len(data)}")
                if gstatus == "0" and len(data) > 2:
                    # JWTToken: field3=token (string)
                    token = _cd2_extract_string_field(data, 3)
                    if not token:
                        logger.error(f"CD2 登录成功但未提取到 token，raw data={data[:80]!r}")
                        return {"ok": False, "message": "CloudDrive2 登录成功但未返回 token"}
                    logger.info("CD2 登录成功，已获取 token")
                else:
                    return {"ok": False, "message": f"CloudDrive2 登录失败 (grpc-status={gstatus})"}
            except Exception as e:
                return {"ok": False, "message": f"CloudDrive2 登录异常: {e}"}
        else:
            logger.warning(f"CD2 无 token 且无用户名密码（username={bool(username)}, password={bool(password)}）")

    # AddOfflineFileRequest: field1=urls(磁力), field2=toFolder(目标文件夹)
    # 注意：CD2 的方法名是 AddOfflineFiles（不是文档旧版的 CreateOfflineTask）
    payload = _cd2_encode_string(1, magnet) + _cd2_encode_string(2, save_path)
    try:
        data, gstatus, httpstatus = await _cd2_grpc_web_call(url, "AddOfflineFiles", payload, token)
        if gstatus == "0":
            return {"ok": True, "message": "已推送到 CloudDrive2"}
        # 常见错误：2=UNKNOWN, 7=PERMISSION_DENIED, 12=UNIMPLEMENTED, 16=UNAUTHENTICATED
        err_map = {"2": "未知错误", "7": "权限不足", "12": "方法不存在", "16": "未认证（token 无效或过期）", "13": "内部错误"}
        msg = err_map.get(gstatus, f"gRPC status={gstatus}")
        return {"ok": False, "message": f"CloudDrive2: {msg}"}
    except Exception as e:
        return {"ok": False, "message": f"连接失败: {e}"}


def _cd2_extract_string_field(data: bytes, field_num: int) -> str:
    """从 protobuf 二进制中提取指定 string 字段（简单解析，不处理嵌套）。"""
    i = 0
    while i < len(data):
        if i >= len(data):
            break
        tag = data[i]
        wire_type = tag & 0x07
        fn = tag >> 3
        i += 1
        if wire_type == 2:  # length-delimited (string/bytes)
            # 读 varint 长度
            length = 0
            shift = 0
            while i < len(data):
                b = data[i]
                i += 1
                length |= (b & 0x7F) << shift
                shift += 7
                if not (b & 0x80):
                    break
            value = data[i:i + length]
            i += length
            if fn == field_num:
                return value.decode("utf-8", errors="replace")
        elif wire_type == 0:  # varint
            while i < len(data):
                b = data[i]
                i += 1
                if not (b & 0x80):
                    break
        elif wire_type == 5:  # 32-bit
            i += 4
        elif wire_type == 1:  # 64-bit
            i += 8
        else:
            break
    return ""


@router.post("/push")
@router.post("/download")  # 兼容前端旧路径
async def push_magnet(req: PushRequest, db: DbSession, _user: CurrentUser):
    """推送磁力到下载器并记录到 downloads 表。"""
    # 读配置
    config = {}
    for k in ["qb_url", "qb_username", "qb_password", "qbittorrent_save_path",
              "aria2_url", "aria2_secret",
              "clouddrive_url", "clouddrive_token", "clouddrive_username", "clouddrive_password", "clouddrive_save_path",
              "transmission_url", "transmission_username", "transmission_password"]:
        config[k] = _get_setting(db, k)

    # 下载器：空时读 DB 的 default_downloader
    downloader = req.downloader or _get_setting(db, "default_downloader") or "qbittorrent"

    # 推送
    logger.info(f"推送磁力到 {downloader}: {req.magnet[:80]}... (task_id={req.task_id})")
    if downloader == "qbittorrent":
        result = await _push_qbittorrent(req.magnet, config)
    elif downloader == "aria2":
        result = await _push_aria2(req.magnet, config)
    elif downloader == "clouddrive":
        result = await _push_clouddrive(req.magnet, config)
    else:
        logger.warning(f"不支持的下载器: {downloader}")
        return {"ok": False, "message": f"暂不支持的下载器: {downloader}"}

    # 记录推送结果
    if result["ok"]:
        logger.info(f"推送成功 [{downloader}]: {result.get('message', '')}")
    else:
        logger.error(f"推送失败 [{downloader}]: {result.get('message', '')}")

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
        result = await asyncio.to_thread(_test_qbittorrent_sync, config)
        logger.info(f"测试连接 [qbittorrent]: ok={result.get('ok')} msg={result.get('message','')}")
        return result
    elif downloader == "aria2":
        if not config["aria2_url"]:
            return {"ok": False, "message": "未配置"}
        return {"ok": True, "message": "配置已读取（连接测试需实际推送）"}
    elif downloader == "clouddrive":
        if not config["clouddrive_url"]:
            return {"ok": False, "message": "未配置"}
        # 真实连接测试：调 GetSystemInfo（公共方法，无需鉴权）
        try:
            data, gstatus, httpstatus = await _cd2_grpc_web_call(config["clouddrive_url"], "GetSystemInfo", b"")
            if gstatus == "0":
                logger.info(f"测试连接 [clouddrive]: ok=True 服务可达")
                return {"ok": True, "message": "CloudDrive2 服务可达"}
            logger.warning(f"测试连接 [clouddrive]: ok=False gRPC status={gstatus}")
            return {"ok": False, "message": f"CloudDrive2 gRPC status={gstatus}"}
        except Exception as e:
            logger.error(f"测试连接 [clouddrive]: 异常 {e}")
            return {"ok": False, "message": f"连接失败: {e}"}
    return {"ok": False, "message": f"未知下载器: {downloader}"}


@router.get("/logs")
def downloader_logs(_user: CurrentUser, limit: int = 100):
    """读取最近的下载器日志（data/downloaders.log）。"""
    from pathlib import Path
    from config import get_settings
    log_path = Path(get_settings().DATA_DIR) / "downloaders.log"
    if not log_path.exists():
        return {"lines": [], "total": 0}
    try:
        # 读最后 N 行
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return {"lines": lines[-limit:], "total": len(lines)}
    except Exception as e:
        return {"lines": [], "total": 0, "error": str(e)}
