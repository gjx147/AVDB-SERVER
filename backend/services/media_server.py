"""媒体服务器集成 —— Emby 在库状态查询与缓存。

Immortal 参考：判断番号是否已在媒体库，缓存到 Task.media_in_library。
配置存 settings 表：emby_url / emby_token / emby_library_id / emby_auto_sync

可靠性设计：
- 精确匹配：searchTerm 是模糊搜索（ABC-123 会命中 ABC-1234），查询后对返回
  条目提取番号规范化比对，完全相等才算在库。
- 查询失败（HTTP≠200/超时/解析错误）返回 None（未知）而非 False，
  调用方按需处理：同步时保持缓存不变，巡检时视为不在库。
- 批量同步：并发（信号量限流）+ 连接复用 + 增量（只查缓存为 NULL 或过期的）。
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select

from database import SessionLocal
from models import Setting, Task

logger = logging.getLogger("avdb.media")

_CODE_RE = re.compile(r"([A-Za-z]{2,6})[-_\s]?(\d{2,5})")

# 同步并发与缓存有效期
_SYNC_CONCURRENCY = 8
_SYNC_TIMEOUT = 10
_CACHE_TTL_HOURS = 24


def normalize_code(code: str) -> str:
    """番号规范化：abc_123 / ABC 123 / ABC123 → ABC-123（大小写统一、分隔符统一）。"""
    m = _CODE_RE.search(code or "")
    if not m:
        return (code or "").strip().upper()
    return f"{m.group(1).upper()}-{m.group(2)}"


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


def _extract_codes_from_item(item: dict) -> set[str]:
    """从 Emby 条目提取候选番号（Name/SeriesName/Path 全找）。"""
    codes: set[str] = set()
    for field in ("Name", "SeriesName", "Path", "FileName"):
        v = item.get(field)
        if v:
            for m in _CODE_RE.finditer(str(v)):
                codes.add(f"{m.group(1).upper()}-{m.group(2)}")
    return codes


async def check_in_library(video_code: str) -> bool | None:
    """查询单个番号是否在 Emby 媒体库。

    返回 True/False；未配置或查询失败返回 None（未知，不等于 False）。
    """
    config = await _get_config()
    url = config.get("emby_url", "").rstrip("/")
    token = config.get("emby_token", "")
    if not url or not token:
        return None
    target = normalize_code(video_code)
    params = {
        "searchTerm": video_code,
        "Recursive": "true",
        "IncludeItemTypes": "Movie,Video",
        "Fields": "Path,SeriesName",
        "api_key": token,
    }
    # 限定媒体库（配置了 emby_library_id 时），避免多库误判
    library_id = config.get("emby_library_id", "")
    if library_id:
        params["ParentId"] = library_id
    try:
        async with httpx.AsyncClient(timeout=_SYNC_TIMEOUT) as client:
            resp = await client.get(f"{url}/emby/Items", params=params)
            if resp.status_code != 200:
                logger.debug(f"Emby 查询 HTTP {resp.status_code} ({video_code})")
                return None
            items = resp.json().get("Items", [])
            # 精确匹配：返回条目的番号需与目标完全相等（防 ABC-123 命中 ABC-1234）
            for item in items:
                if target in _extract_codes_from_item(item):
                    return True
            return False
    except Exception as e:
        logger.debug(f"Emby 查询失败({video_code}): {e}")
        return None


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
        async with httpx.AsyncClient(timeout=_SYNC_TIMEOUT) as client:
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


async def _check_many(video_codes: list[str]) -> dict[str, bool | None]:
    """并发查询一批番号（连接复用 + 信号量限流）。返回 {code: True/False/None}。"""
    config = await _get_config()
    url = config.get("emby_url", "").rstrip("/")
    token = config.get("emby_token", "")
    if not url or not token:
        return {c: None for c in video_codes}
    library_id = config.get("emby_library_id", "")
    sem = asyncio.Semaphore(_SYNC_CONCURRENCY)
    results: dict[str, bool | None] = {}

    async with httpx.AsyncClient(timeout=_SYNC_TIMEOUT) as client:
        async def one(code: str) -> None:
            params = {
                "searchTerm": code,
                "Recursive": "true",
                "IncludeItemTypes": "Movie,Video",
                "Fields": "Path,SeriesName",
                "api_key": token,
            }
            if library_id:
                params["ParentId"] = library_id
            async with sem:
                try:
                    resp = await client.get(f"{url}/emby/Items", params=params)
                    if resp.status_code != 200:
                        results[code] = None
                        return
                    items = resp.json().get("Items", [])
                    target = normalize_code(code)
                    results[code] = any(
                        target in _extract_codes_from_item(it) for it in items
                    )
                except Exception:
                    results[code] = None

        await asyncio.gather(*(one(c) for c in video_codes))
    return results


async def sync_library_status(limit: int = 200, force: bool = False) -> dict:
    """批量同步在库状态（并发 + 增量）。

    - force=False（默认增量）：只查缓存为 NULL 或超过 TTL 的任务
    - 查询失败（None）不写库，保持原缓存不被污染
    """
    db = SessionLocal()
    try:
        stmt = select(Task).where(Task.video_code.isnot(None))
        if not force:
            expired_before = datetime.utcnow() - timedelta(hours=_CACHE_TTL_HOURS)
            stmt = stmt.where(
                (Task.media_in_library.is_(None))
                | (Task.updated_at < expired_before)
            )
        tasks = db.execute(stmt.order_by(Task.id.desc()).limit(limit)).scalars().all()
        if not tasks:
            return {"ok": True, "checked": 0, "in_library": 0, "failed": 0}

        codes = [t.video_code for t in tasks]
        results = await _check_many(codes)

        checked = in_lib = failed = 0
        for t in tasks:
            r = results.get(t.video_code)
            if r is None:
                failed += 1  # 查询失败：不写库，保持原值
                continue
            t.media_in_library = r
            checked += 1
            if r:
                in_lib += 1
        db.commit()
        logger.info(f"[媒体库同步] checked={checked} in_library={in_lib} failed={failed}")
        return {"ok": True, "checked": checked, "in_library": in_lib, "failed": failed}
    finally:
        db.close()


async def run_scheduled_sync() -> dict:
    """定时同步入口：emby_auto_sync 未开启或未配置 Emby 时跳过。"""
    config = await _get_config()
    if not config.get("emby_url") or not config.get("emby_token"):
        return {"ok": False, "message": "未配置 Emby"}
    db = SessionLocal()
    try:
        row = db.get(Setting, "emby_auto_sync")
        if not (row and row.value and row.value.lower() == "true"):
            return {"ok": False, "message": "emby_auto_sync 未启用"}
    finally:
        db.close()
    return await sync_library_status(limit=500)


def register_job() -> None:
    """注册每日定时同步到调度中心。"""
    from services.scheduler import add_interval_job
    add_interval_job(run_scheduled_sync, "media-library-sync", seconds=24 * 3600)
    logger.info("媒体库定时同步已注册: 每 24h")
