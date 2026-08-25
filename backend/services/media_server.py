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


def _library_ids(config: dict) -> list[str]:
    """解析 emby_library_id 配置：支持多个库 ID，逗号/中文逗号/空格分隔。空 → []（不限库）。"""
    raw = config.get("emby_library_id", "")
    if not raw:
        return []
    parts = re.split(r"[,，\s]+", raw.strip())
    return [p for p in parts if p]


async def _query_one(
    client: httpx.AsyncClient, url: str, token: str, library_id: str, video_code: str,
) -> bool | None:
    """在单个范围内查询番号（library_id 空 = 全库）。精确匹配；失败返回 None。"""
    params = {
        "searchTerm": video_code,
        "Recursive": "true",
        "IncludeItemTypes": "Movie,Video",
        "Fields": "Path,SeriesName",
        "api_key": token,
    }
    if library_id:
        params["ParentId"] = library_id
    try:
        resp = await client.get(f"{url}/emby/Items", params=params)
        if resp.status_code != 200:
            logger.debug(f"Emby 查询 HTTP {resp.status_code} ({video_code}, lib={library_id or 'all'})")
            return None
        items = resp.json().get("Items", [])
        target = normalize_code(video_code)
        # 精确匹配：返回条目的番号需与目标完全相等（防 ABC-123 命中 ABC-1234）
        return any(target in _extract_codes_from_item(it) for it in items)
    except Exception as e:
        logger.debug(f"Emby 查询失败({video_code}, lib={library_id or 'all'}): {e}")
        return None


def _combine(results: list[bool | None]) -> bool | None:
    """合并多库结果：任一 True → True；否则任一有效 False → False；全失败 → None。"""
    if any(r is True for r in results):
        return True
    if any(r is False for r in results):
        return False
    return None


async def check_in_library(video_code: str) -> bool | None:
    """查询单个番号是否在 Emby 媒体库。

    返回 True/False；未配置或查询失败返回 None（未知，不等于 False）。
    配置了多个库 ID 时逐库并发查询，任一命中即在库。
    """
    config = await _get_config()
    url = config.get("emby_url", "").rstrip("/")
    token = config.get("emby_token", "")
    if not url or not token:
        return None
    libs = _library_ids(config)
    scopes = libs or [""]
    async with httpx.AsyncClient(timeout=_SYNC_TIMEOUT) as client:
        results = await asyncio.gather(*(
            _query_one(client, url, token, lib, video_code) for lib in scopes
        ))
    return _combine(list(results))


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
    """并发查询一批番号（连接复用 + 信号量限流 + 多库逐库合并）。返回 {code: True/False/None}。"""
    config = await _get_config()
    url = config.get("emby_url", "").rstrip("/")
    token = config.get("emby_token", "")
    if not url or not token:
        return {c: None for c in video_codes}
    scopes = _library_ids(config) or [""]
    sem = asyncio.Semaphore(_SYNC_CONCURRENCY)
    results: dict[str, bool | None] = {}

    async with httpx.AsyncClient(timeout=_SYNC_TIMEOUT) as client:
        async def one_scope(code: str, lib: str) -> bool | None:
            async with sem:
                return await _query_one(client, url, token, lib, code)

        async def one(code: str) -> None:
            per_lib = await asyncio.gather(*(one_scope(code, lib) for lib in scopes))
            results[code] = _combine(list(per_lib))

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
    # F10: 观看进度同步（每 6 小时，播放记录更新及时反映）
    async def _view_sync_wrapper() -> dict:
        return await sync_view_progress()
    add_interval_job(_view_sync_wrapper, "emby-view-progress-sync", seconds=6 * 3600)
    logger.info("媒体库定时同步已注册: 在库 24h / 观看进度 6h")


async def sync_view_progress(threshold: float = 90.0, limit: int = 500) -> dict:
    """F10: 从 Emby 拉取媒体库播放进度，已观看（>threshold%）的自动标记本地已看。"""
    from sqlalchemy import select

    from database import SessionLocal
    from models import Task

    config = await _get_config()
    url = config.get("emby_url", "").rstrip("/")
    token = config.get("emby_token", "")
    if not url or not token:
        return {"ok": False, "message": "未配置 Emby"}
    headers = {"X-Emby-Token": token}
    params = {"Recursive": "true", "Fields": "Path,PlayedPercentage", "Limit": limit}

    # API Key 无用户上下文：先取第一个用户 id 再查其进度
    user_id = ""
    try:
        async with httpx.AsyncClient(timeout=15, verify=False) as c:
            r = await c.get(f"{url}/emby/Users", headers=headers)
            users = r.json() if r.status_code == 200 else []
            if users:
                user_id = str(users[0].get("Id", ""))
    except Exception:
        pass

    rows_all: list[dict] = []
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        for lib in _library_ids(config) or [None]:
            p = dict(params)
            if lib:
                p["ParentId"] = lib
            base = f"{url}/emby/Users/{user_id}/Items" if user_id else f"{url}/emby/Items"
            try:
                r = await client.get(base, headers=headers, params=p)
                if r.status_code == 200:
                    rows_all.extend(r.json().get("Items", []))
            except Exception as e:
                logger.warning(f"Emby 进度查询失败({lib}): {e}")

    watched = [it for it in rows_all if (it.get("PlayedPercentage") or 0) >= threshold]
    if not watched:
        return {"ok": True, "scanned": len(rows_all), "watched": 0, "marked": 0}

    codes: set[str] = set()
    for it in watched:
        codes.update(_extract_codes_from_item(it))
    if not codes:
        return {"ok": True, "scanned": len(rows_all), "watched": len(watched), "marked": 0}

    db = SessionLocal()
    try:
        tasks = db.execute(
            select(Task).where(
                Task.video_code.in_(codes),
                (Task.view_status.is_(None) | (Task.view_status != "viewed")),
            )
        ).scalars().all()
        marked = 0
        for t in tasks:
            t.view_status = "viewed"
            marked += 1
        db.commit()
        logger.info("Emby 观看进度同步：%d 条已看（库内匹配 %d）", len(watched), marked)
        return {"ok": True, "scanned": len(rows_all), "watched": len(watched), "marked": marked}
    finally:
        db.close()


async def audit_library() -> dict:
    """N18: Emby 反向审计——Emby 有但库无 / 本地重复番号 / 库在档但 Emby 缺失。"""
    import re as _re
    from collections import Counter
    from sqlalchemy import select

    from database import SessionLocal
    from models import Task

    config = await _get_config()
    url = config.get("emby_url", "").rstrip("/")
    token = config.get("emby_token", "")
    if not url or not token:
        return {"ok": False, "message": "未配置 Emby"}
    headers = {"X-Emby-Token": token}
    emby_codes: set[str] = set()
    emby_paths: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=30, verify=False) as c:
            r = await c.get(f"{url}/emby/Items", headers=headers,
                            params={"Recursive": "true", "Fields": "Path", "Limit": 5000})
            items = r.json().get("Items", []) if r.status_code == 200 else []
            for it in items:
                p = str(it.get("Path") or "")
                if p:
                    emby_paths.append(p)
                emby_codes.update(_extract_codes_from_item(it))
    except Exception as e:
        return {"ok": False, "message": f"Emby 查询失败: {str(e)[:200]}"}

    db = SessionLocal()
    try:
        local = db.execute(select(Task.video_code, Task.media_in_library)).all()
        local_codes = {c for c, _ in local if c}
        code_counter = Counter(c for c, _ in local if c)
        dup_codes = [{"code": c, "count": n} for c, n in code_counter.items() if n > 1][:20]
        emby_only = sorted(emby_codes - local_codes)[:50]
        lib_missing = [c for c, in_lib in local if in_lib and c and c not in emby_codes][:50]
        return {
            "ok": True,
            "emby_total": len(emby_paths),
            "local_total": len(local_codes),
            "emby_only": emby_only,
            "dup_codes": dup_codes,
            "in_lib_missing_from_emby": lib_missing,
        }
    finally:
        db.close()
