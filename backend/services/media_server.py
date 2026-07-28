"""媒体服务器集成 —— Emby/Jellyfin 在库状态查询与缓存。

Immortal 参考：判断番号是否已在媒体库，缓存到 Task.media_in_library。
配置存 settings 表：emby_url / emby_token / emby_library_id
"""

from __future__ import annotations

import logging
import re

import httpx
from sqlalchemy import select

from database import SessionLocal
from models import Setting, Task

logger = logging.getLogger("avdb.media")

_CODE_RE = re.compile(r"([A-Za-z]{2,6})[-_]?\s*(\d{2,5})")


async def _get_config() -> dict[str, str]:
    db = SessionLocal()
    try:
        result = {}
        for k in ["emby_url", "emby_token", "emby_library_id"]:
            row = db.get(Setting, k)
            if row and row.value:
                result[k] = row.value
        return result
    finally:
        db.close()


async def check_in_library(video_code: str) -> bool:
    """查询单个番号是否在 Emby 媒体库。"""
    config = await _get_config()
    url = config.get("emby_url", "").rstrip("/")
    token = config.get("emby_token", "")
    if not url or not token:
        return False
    # Emby 搜索 API
    search_url = f"{url}/emby/Items?searchTerm={video_code}&Recursive=true&IncludeItemTypes=Movie&api_key={token}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(search_url)
            if resp.status_code != 200:
                return False
            data = resp.json()
            return data.get("TotalRecordCount", 0) > 0
    except Exception as e:
        logger.debug(f"Emby 查询失败({video_code}): {e}")
        return False


async def test_connection(url: str = "", token: str = "") -> dict:
    """测试 Emby 连接（调 /emby/System/Info 验证）。"""
    if not url:
        config = await _get_config()
        url = config.get("emby_url", "").rstrip("/")
        token = config.get("emby_token", "")
    if not url:
        return {"ok": False, "message": "未配置 emby_url"}
    if not token:
        return {"ok": False, "message": "未配置 emby_token"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{url.rstrip('/')}/emby/System/Info",
                params={"api_key": token},
            )
            if resp.status_code == 200:
                info = resp.json()
                return {
                    "ok": True,
                    "message": f"Emby 可达: {info.get('ServerName', '?')} {info.get('Version', '?')}",
                }
            return {"ok": False, "message": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"ok": False, "message": f"连接失败: {e}"}


async def sync_library_status(limit: int = 200) -> dict:
    """批量同步在库状态：扫描所有有番号的任务，查询并缓存。"""
    db = SessionLocal()
    try:
        tasks = db.execute(
            select(Task).where(Task.video_code.isnot(None)).limit(limit)
        ).scalars().all()
        checked = 0
        in_lib = 0
        for task in tasks:
            result = await check_in_library(task.video_code)
            task.media_in_library = result
            checked += 1
            if result:
                in_lib += 1
        db.commit()
        return {"ok": True, "checked": checked, "in_library": in_lib}
    finally:
        db.close()
