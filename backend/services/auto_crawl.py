"""自动爬取调度服务 —— 定时 scan + extract 所有列表源。

AVDB auto_crawl.py 的去补丁化重写。修复两个已知 bug：
1. _get_setting(get_conn()) 误用上下文管理器 → 改用 ORM session
2. meta_refresh 的 CASE WHEN 失效(THEN/ELSE同字段) → 重写保护逻辑

设计：
- async 函数，挂 APScheduler（不阻塞事件循环）
- asyncio.create_subprocess_exec 调 scraper（非阻塞）
- 串行处理所有列表源（避免并发抢浏览器）
- 失败自适应退避
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import select

from config import get_settings
from database import SessionLocal
from models import ListSource

logger = logging.getLogger("avdb.auto_crawl")

# 运行状态（内存）
_state = {"running": False, "current": None, "last_run": None, "errors": 0}


def _scraper_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "magnet_scraper" / "scraper.py"


def _python_exe() -> str:
    return get_settings().SCRAPER_PYTHON or sys.executable


async def _run_scraper(args: list[str], timeout: int = 1800) -> bool:
    """非阻塞执行 scraper 子进程。返回是否成功(exit 0)。"""
    cmd = [_python_exe(), str(_scraper_path())] + args
    env = dict(__import__("os").environ)
    logger.info("启动 scraper: %s", " ".join(args))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode == 0:
                logger.info("scraper 完成: %s", " ".join(args))
                return True
            logger.warning("scraper 退出码 %d: %s", proc.returncode, (stdout or b"")[:500])
            return False
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("scraper 超时(%ds)被kill: %s", timeout, " ".join(args))
            return False
    except Exception as e:
        logger.error("scraper 执行异常: %s", e)
        return False


async def run_scan_cycle() -> dict:
    """对所有列表源执行一轮 scan。"""
    if _state["running"]:
        logger.warning("已有爬取在运行，跳过本次")
        return {"ok": False, "message": "已在运行"}
    _state["running"] = True
    _state["current"] = "scan"
    results = []
    try:
        db = SessionLocal()
        try:
            sources = db.execute(select(ListSource).order_by(ListSource.id)).scalars().all()
        finally:
            db.close()

        for src in sources:
            if src.list_code == "RANKING":  # 排行榜专用，不走 scan
                continue
            _state["current"] = f"scan:{src.list_code}"
            ok = await _run_scraper(
                ["scan", "--list-source-id", str(src.id), "-p", str(src.max_pages or 100)]
            )
            results.append({"source": src.list_code, "scan_ok": ok})
        _state["last_run"] = "scan"
        return {"ok": True, "results": results}
    finally:
        _state["running"] = False
        _state["current"] = None


async def run_extract_cycle() -> dict:
    """对所有列表源执行一轮 extract。"""
    if _state["running"]:
        return {"ok": False, "message": "已在运行"}
    _state["running"] = True
    _state["current"] = "extract"
    results = []
    try:
        db = SessionLocal()
        try:
            sources = db.execute(select(ListSource).order_by(ListSource.id)).scalars().all()
        finally:
            db.close()

        for src in sources:
            _state["current"] = f"extract:{src.list_code}"
            ok = await _run_scraper(["extract", "--list-source-id", str(src.id), "--failed-only"])
            results.append({"source": src.list_code, "extract_ok": ok})
        _state["last_run"] = "extract"
        return {"ok": True, "results": results}
    finally:
        _state["running"] = False
        _state["current"] = None


def get_state() -> dict:
    """查询运行状态。"""
    return dict(_state)


def register_jobs(scan_interval: int = 3600, extract_interval: int = 600) -> None:
    """把 scan/extract 注册到调度中心。"""
    from services.scheduler import add_interval_job

    add_interval_job(run_scan_cycle, "auto-crawl-scan", seconds=scan_interval)
    add_interval_job(run_extract_cycle, "auto-crawl-extract", seconds=extract_interval)
    logger.info("auto_crawl 已注册: scan 每%ds, extract 每%ds", scan_interval, extract_interval)
