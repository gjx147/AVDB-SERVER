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


async def _push_clouddrive(magnet: str, config: dict) -> dict:
    """推送到 CloudDrive2（gRPC-Web 协议，复用 services.cd2_client）。"""
    from services.cd2_client import get_token_or_login, add_offline_files
    url = config.get("clouddrive_url", "")
    if not url:
        return {"ok": False, "message": "CloudDrive2 未配置"}
    save_path = config.get("clouddrive_save_path", "/")

    # 鉴权：优先 token；否则用户名密码 GetToken
    token, err = await get_token_or_login(config)
    if err:
        # 登录失败但若有 token 仍可尝试（token 字段可能被 settings 脱敏为 ***，此处已读真实值）
        return {"ok": False, "message": err}

    ok, msg = await add_offline_files(url, token, magnet, save_path)
    return {"ok": ok, "message": msg}


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

    # 推送成功 → 触发 CMS 后处理（延迟整理 + 生成 strm）
    # 异常隔离：CMS 钩子失败绝不影响 push 的成功状态
    if result["ok"]:
        try:
            from services.cms_sync import schedule_sync
            schedule_sync(req.magnet, task.video_code if task else None)
        except Exception as e:
            logger.warning(f"CMS 钩子调度失败（不影响推送）: {e}")

    # 推送成功 → 触发 CD2 自动迁移（MoveFile 到媒体库女优子目录 + 通知 CMS）
    # 异常隔离：CD2 迁移钩子失败绝不影响 push 的成功状态
    if result["ok"]:
        try:
            from services.cd2_organize import schedule_organize
            schedule_organize(req.task_id, task.video_code if task else None)
        except Exception as e:
            logger.warning(f"CD2 迁移钩子调度失败（不影响推送）: {e}")

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
              "clouddrive_url", "clouddrive_token", "clouddrive_username", "clouddrive_password",
              "cms_url", "cms_token",
              "cd2_organize_source_folder", "cd2_organize_target_folder"]:
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
            from services.cd2_client import grpc_web_call
            data, gstatus, httpstatus = await grpc_web_call(config["clouddrive_url"], "GetSystemInfo", b"")
            if gstatus == "0":
                logger.info(f"测试连接 [clouddrive]: ok=True 服务可达")
                return {"ok": True, "message": "CloudDrive2 服务可达"}
            logger.warning(f"测试连接 [clouddrive]: ok=False gRPC status={gstatus}")
            return {"ok": False, "message": f"CloudDrive2 gRPC status={gstatus}"}
        except Exception as e:
            logger.error(f"测试连接 [clouddrive]: 异常 {e}")
            return {"ok": False, "message": f"连接失败: {e}"}
    elif downloader == "cms":
        # CMS 是后处理钩子，复用 auto_organize（幂等可安全测试）
        from services.cms_sync import test_connection as _cms_test
        result = await _cms_test(config["cms_url"], config["cms_token"])
        logger.info(f"测试连接 [cms]: ok={result.get('ok')} msg={result.get('message','')}")
        return result
    elif downloader == "cd2_organize":
        # CD2 迁移测试：列源文件夹验证 CD2 连接 + 路径配置
        from services.cd2_organize import test_organize
        result = await test_organize(config)
        logger.info(f"测试连接 [cd2_organize]: ok={result.get('ok')} msg={result.get('message','')}")
        return result
    return {"ok": False, "message": f"未知下载器: {downloader}"}


@router.get("/list-cd2-folder")
async def list_cd2_folder(path: str, db: DbSession, _user: CurrentUser):
    """调试端点：列出 CD2 任意路径的目录内容，用于排查 cd2_organize 路径问题。

    Query 参数 path：要列的 CD2 路径（如 / 或 /115open）
    """
    from services.cd2_client import get_token_or_login, list_folder
    config = {}
    for k in ["clouddrive_url", "clouddrive_token", "clouddrive_username", "clouddrive_password"]:
        config[k] = _get_setting(db, k)
    token, err = await get_token_or_login(config)
    if err:
        return {"ok": False, "message": err}
    files, list_err = await list_folder(config["clouddrive_url"], token, path)
    if list_err:
        return {"ok": False, "message": f"列目录失败: {list_err}", "path": path}
    return {
        "ok": True,
        "path": path,
        "count": len(files),
        "items": [
            {
                "name": f["name"],
                "full_path": f["full_path"],
                "is_directory": f["is_directory"],
                "size": f["size"],
            }
            for f in files[:50]
        ],
    }


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
