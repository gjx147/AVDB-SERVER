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
from deps import CurrentAdmin, CurrentUser, DbSession
from models import Download, Setting, Task

logger = logging.getLogger("avdb.downloaders")

router = APIRouter(prefix="/api/downloaders", tags=["downloaders"])


def _get_setting(db, key: str) -> str:
    """读 settings 表（统一入口：含 qbittorrent_* 别名兼容，见 services/settings_util）。"""
    from services.settings_util import get_setting
    return get_setting(db, key)


def _extract_hash(magnet: str) -> str | None:
    m = re.search(r"btih:([a-fA-F0-9]{40})", magnet)
    return m.group(1).lower() if m else None


# 演员分文件夹：一级总目录（口径确认：女优/演员名/）
_ACTOR_SUBDIR = "女优"

# 公共 tracker 默认列表（磁力无 tr= 时附加；来源 ngosang/trackerslist trackers_best.txt，
# 2026-09-07 实时拉取 20 条）。HTTPS/TCP 项排前：服务器 UDP 被封锁时 TCP 443 仍可连接，
# 是「qB 卡元数据而迅雷（走私有加速网）能下」这一场景的最优解；可用设置键
# qb_global_trackers 覆盖为最新 trackers_best.txt 全量列表。
_DEFAULT_TRACKERS = ",".join([
    "https://tracker.pmman.tech:443/announce",
    "https://tracker.nekomi.cn:443/announce",
    "https://tracker.bt4g.com:443/announce",
    "https://pybittrack.retiolus.net:443/announce",
    "https://ht.therarbg.to:443/announce",
    "https://004430.xyz:443/announce",
    "udp://zer0day.ch:1337/announce",
    "udp://tracker.therarbg.to:6969/announce",
    "udp://tracker.publictracker.xyz:6969/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker2.dler.org:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://tracker.qu.ax:6969/announce",
    "udp://tracker.dler.org:6969/announce",
    "udp://tracker.auctor.tv:6969/announce",
    "udp://retracker01-msk-virt.corbina.net:80/announce",
    "udp://open.stealth.si:80/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://bittorrent-tracker.e-n-c-r-y-p-t.net:1337/announce",
])


_WIN_RESERVED = re.compile(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$")


def _safe_folder_name(name: str) -> str | None:
    """文件系统消毒：非法字符替换 _、去首尾空白与点、限长；空/点/点点/Windows 保留名拒绝。"""
    n = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", (name or "").strip()).strip(". ")
    if not n or n in (".", "..") or _WIN_RESERVED.fullmatch(n):
        return None
    return n[:100].rstrip(". ") or None  # 截断后不留尾部点/空格（Windows 不允许）


def _first_actor_name(task) -> str | None:
    """作品首位女优（JavDB 演员文本女优在前，逗号分隔）。"""
    if task is None or not task.actors:
        return None
    return (task.actors.split(",")[0] or "").strip() or None


class PushRequest(BaseModel):
    magnet: str
    task_id: int | None = None
    downloader: str = "xunlei"  # xunlei/clouddrive/aria2（qB 已退役）


async def _push_xunlei(magnet: str, config: dict) -> dict:
    """推送到迅雷容器（cnk3x/xunlei Web 接口）。"""
    from services.xunlei_client import XunleiClient
    url = config.get("xunlei_url", "")
    if not url:
        return {"ok": False, "message": "迅雷未配置"}
    try:
        client = XunleiClient(url,
                              basic_user=config.get("xunlei_basic_user", ""),
                              basic_pass=config.get("xunlei_basic_pass", ""))
        # 任务名 = 番号（轮询按 video_code 匹配）；无名则固定前缀
        name = config.get("_task_name", "") or "avdb-task"
        return client.add_task(magnet, name)
    except Exception as e:
        return {"ok": False, "message": str(e)[:200]}


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
    for k in ["xunlei_url", "xunlei_basic_user", "xunlei_basic_pass",
              "aria2_url", "aria2_secret",
              "clouddrive_url", "clouddrive_token", "clouddrive_username", "clouddrive_password", "clouddrive_save_path",
              "transmission_url", "transmission_username", "transmission_password"]:
        config[k] = _get_setting(db, k)

    # 下载器：空时读 DB 的 default_downloader
    downloader = req.downloader or _get_setting(db, "default_downloader") or "xunlei"
    if downloader == "qbittorrent":
        downloader = "xunlei"  # qB 退役归一（旧默认值/策略残留，S3）

    # 推送
    logger.info(f"推送磁力到 {downloader}: {req.magnet[:80]}... (task_id={req.task_id})")
    # 提前取任务：推送时以番号作为迅雷任务名（轮询按 video_code 匹配）
    task = db.get(Task, req.task_id) if req.task_id else None
    if task and task.video_code:
        config["_task_name"] = task.video_code
    if downloader == "xunlei":
        if not task or not task.video_code:
            return {"ok": False, "message": "迅雷通道需要作品番号（无番号作品请用「导出磁力」手动添加）"}
        config["_task_name"] = task.video_code
        result = await _push_xunlei(req.magnet, config)
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

    # CloudDrive2 推送成功 → 延迟整理下载文件（≥200MB 视频重命名为番号，其余删除）
    # 开关 cd2_rename_enabled 默认关；异常隔离，绝不影响 push 的成功状态
    if result["ok"] and downloader == "clouddrive":
        try:
            from services.cd2_rename import schedule_rename
            schedule_rename(req.task_id, task.video_code if task else None)
        except Exception as e:
            logger.warning(f"CD2 整理钩子调度失败（不影响推送）: {e}")

    return {"ok": result["ok"], "download_id": dl.id, "message": result.get("message")}


@router.post("/rename-all")
def cd2_rename_all(_admin: CurrentAdmin):
    """CD2 一键整理：全部已推送未整理的 clouddrive 记录立即整理。"""
    import asyncio as _aio
    from services.cd2_rename import run_rename_all
    try:
        loop = _aio.get_event_loop()
    except RuntimeError:
        loop = _aio.new_event_loop()
        _aio.set_event_loop(loop)
    r = loop.run_until_complete(_aio.to_thread(run_rename_all))
    return r


@router.post("/test")
@router.post("/test-connection")  # 兼容前端旧路径
async def test_connection(body: dict, db: DbSession, _user: CurrentUser):
    """测试下载器连接（qB 同步调用包 to_thread，不阻塞事件循环）。

    前端 POST body: {downloader, save_path?}
    """
    import asyncio
    downloader = body.get("downloader", "")
    config = {}
    for k in ["xunlei_url", "xunlei_basic_user", "xunlei_basic_pass", "aria2_url", "aria2_secret",
              "clouddrive_url", "clouddrive_token", "clouddrive_username", "clouddrive_password",
              "cd2_download_folder"]:
        config[k] = _get_setting(db, k)
    if downloader == "xunlei":
        from services.xunlei_client import XunleiClient
        result = await asyncio.to_thread(
            lambda: XunleiClient(config.get("xunlei_url", ""),
                                 config.get("xunlei_basic_user", ""),
                                 config.get("xunlei_basic_pass", "")).test())
        if result.get("ok"):
            result = {**result, "message": f"迅雷连通：版本 {result.get('version')}，设备 {result.get('device')}"}


        logger.info(f"测试连接 [xunlei]: ok={result.get('ok')} msg={result.get('message','')}")
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
    elif downloader == "cd2_rename":
        # CD2 整理测试：列下载文件夹验证 CD2 连接 + 路径配置
        from services.cd2_rename import test_rename
        result = await test_rename(config)
        logger.info(f"测试连接 [cd2_rename]: ok={result.get('ok')} msg={result.get('message','')}")
        return result
    return {"ok": False, "message": f"未知下载器: {downloader}"}


@router.get("/list-cd2-folder")
async def list_cd2_folder(path: str, db: DbSession, _user: CurrentUser):
    """调试端点：列出 CD2 任意路径的目录内容（排查 CloudDrive2 挂载路径问题）。

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
def downloader_logs(_user: CurrentAdmin, limit: int = 100):
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
