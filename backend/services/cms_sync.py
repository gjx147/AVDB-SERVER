"""CMS 后处理钩子 —— 推送成功后延迟触发 CMS（云端媒体库同步工具）的 auto_organize。

背景：NAS 上 CloudDrive2 和 CMS 共享云盘 —— CD2 把磁力下载到云盘的"离线下载文件夹"，
CMS 扫描源文件夹、整理到媒体库目录并生成 .strm。本模块只在推送成功后触发 CMS 的同步端点，
实际转移与 strm 生成由 CMS 服务完成。

CMS API（token 由启动变量 CMS_API_TOKEN 指定，默认 cloud_media_sync）：
- GET /api/sync/lift_by_token?type=auto_organize&token=xxx  —— 增量同步+自动整理+生成 strm

settings 表 key：
- cms_enabled: 后处理总开关（"true"/"1"/"yes"/"on" 视为启用）
- cms_url: CMS 服务器地址（如 http://192.168.1.x:8080）
- cms_token: API token（默认 cloud_media_sync）
- cms_delay_seconds: 推送成功后延迟触发秒数（默认 60，给下载器时间下载文件）
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger("avdb.downloaders.cms")


def _get_config() -> dict[str, str]:
    """从 settings 表读 CMS 配置（独立 session，避免跨请求复用）。"""
    from database import SessionLocal
    from models import Setting
    keys = ["cms_enabled", "cms_url", "cms_token", "cms_delay_seconds"]
    db = SessionLocal()
    try:
        result = {}
        for k in keys:
            row = db.get(Setting, k)
            if row and row.value is not None:
                result[k] = row.value
        return result
    finally:
        db.close()


def _to_bool(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


async def _trigger_sync(url: str, token: str) -> dict:
    """真正调用 CMS auto_organize。返回 CMS 响应摘要。"""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            url.rstrip("/") + "/api/sync/lift_by_token",
            params={"type": "auto_organize", "token": token},
        )
        return {"status_code": r.status_code, "body": (r.text or "")[:500]}


async def _delayed_sync(magnet: str, video_code: str | None, delay: int):
    """延迟任务：sleep(delay) 后再次读最新配置并触发 CMS 同步。

    延迟期间用户可能改动配置，所以真正调用前重新读取一次 cms_url/cms_token。
    """
    label = f"video_code={video_code}" if video_code else "未知番号"
    logger.info(f"[CMS] 计划在 {delay}s 后触发 auto_organize ({label})")
    try:
        await asyncio.sleep(delay)
        cfg = _get_config()
        url = cfg.get("cms_url", "")
        token = cfg.get("cms_token", "") or "cloud_media_sync"
        if not url:
            logger.warning(f"[CMS] 跳过：cms_url 未配置 ({label})")
            return
        logger.info(f"[CMS] 开始触发 auto_organize ({label})")
        result = await _trigger_sync(url, token)
        if result["status_code"] == 200:
            logger.info(f"[CMS] 同步触发成功 ({label}): {result['body'][:200]}")
        else:
            logger.error(
                f"[CMS] 同步触发失败 ({label}): HTTP {result['status_code']} {result['body'][:200]}"
            )
    except asyncio.CancelledError:
        logger.info(f"[CMS] 延迟任务已取消 ({label})")
        raise
    except Exception as e:
        logger.error(f"[CMS] 延迟任务异常 ({label}): {e}")


def schedule_sync(magnet: str, video_code: str | None) -> None:
    """推送成功后调用此函数。

    若 cms_enabled 启用，则 fire-and-forget 启动一个延迟任务，不阻塞调用方，
    所有异常隔离（绝不影响 push 的成功状态）。
    """
    try:
        cfg = _get_config()
        if not _to_bool(cfg.get("cms_enabled")):
            return  # 未启用，静默跳过（不打日志，避免每次推送都刷屏）
        try:
            delay = int(cfg.get("cms_delay_seconds", "") or "60")
        except ValueError:
            delay = 60
        if delay < 0:
            delay = 0
        # fire-and-forget：创建任务但不 await，loop 持有引用即可
        asyncio.create_task(_delayed_sync(magnet, video_code, delay))
    except Exception as e:
        logger.warning(f"[CMS] schedule_sync 异常（不影响推送）: {e}")


async def test_connection(url: str, token: str) -> dict:
    """测试 CMS 服务可达性（复用 auto_organize，幂等可安全重复触发）。

    供 downloaders.test_connection 的 cms 分支调用。
    """
    if not url:
        return {"ok": False, "message": "未配置 cms_url"}
    if not token:
        token = "cloud_media_sync"
    try:
        result = await _trigger_sync(url, token)
        if result["status_code"] == 200:
            return {"ok": True, "message": "CMS 服务可达（已触发一次同步）"}
        return {"ok": False, "message": f"CMS HTTP {result['status_code']}"}
    except Exception as e:
        return {"ok": False, "message": f"连接失败: {e}"}
