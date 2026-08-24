"""自动爬取调度服务 —— 定时 scan + extract 所有列表源。

AVDB auto_crawl.py 的去补丁化重写。修复两个已知 bug：
1. _get_setting(get_conn()) 误用上下文管理器 → 改用 ORM session
2. meta_refresh 的 CASE WHEN 失效(THEN/ELSE同字段) → 重写保护逻辑

设计：
- async 函数，挂 APScheduler（不阻塞事件循环）
- asyncio.create_subprocess_exec 调 scraper（非阻塞）
- 串行处理所有列表源（避免并发抢浏览器）
- 进程树杀（start_new_session + 整组 kill）
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from config import get_settings
from database import SessionLocal
from models import ListSource

logger = logging.getLogger("avdb.auto_crawl")

# 运行状态（内存）
_state = {"running": False, "current": None, "last_run": None, "errors": 0}

# 批量任务超时（秒）＝ 4 小时。
# 计算依据（F08）：单任务 extract 约 90-120s；单个列表源一轮 extract 可达 100+
# pending/failed 任务，约 2.5-3.5 小时；scan/ranking 又按源/榜串行叠加。
# 旧默认 1800s（30 分钟）会常规性杀断 extract/ranking 长任务，故提到 4 小时留足余量。
# 手动/交互路径不经过本函数（见 routers/crawl.py 的 _DEFAULT_TIMEOUT=1800，
# 由前端可见的 30 分钟超时回收兜底）。调用方仍可传 timeout= 覆盖。
_BATCH_TIMEOUT = 14400  # 4 小时


def _scraper_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "magnet_scraper" / "scraper.py"


def _python_exe() -> str:
    return get_settings().SCRAPER_PYTHON or sys.executable


async def _run_scraper(args: list[str], timeout: int = _BATCH_TIMEOUT) -> bool:
    """非阻塞执行 scraper 子进程。返回是否成功(exit 0)。

    架构修复：start_new_session 创建进程组，超时时整组 kill（Chromium 子树不残留）。
    从 DB settings 注入 proxy + javdb_url 到子进程 env。
    """
    cmd = [_python_exe(), str(_scraper_path())] + args
    env = dict(os.environ)

    # Phase 2 F07：注入回调共享密钥（与 crawl.py _start_scraper 一致，
    # 否则子进程 register/unregister 回调被 401 拒绝）
    from services import scraper_lock
    env["SCRAPER_CALLBACK_TOKEN"] = scraper_lock.get_callback_token()

    # 从 DB settings 读 proxy + javdb_url（与 crawl.py _start_scraper 一致）
    try:
        db = SessionLocal()
        try:
            from models import Setting
            for key in ("http_proxy",):
                row = db.get(Setting, key)
                if row and row.value:
                    val = row.value.strip()
                    env["HTTP_PROXY"] = val
                    env["HTTPS_PROXY"] = val
                    env["http_proxy"] = val
                    env["https_proxy"] = val
            row = db.get(Setting, "javdb_url")
            if row and row.value:
                env["JAVDB_URL"] = row.value.strip()
        finally:
            db.close()
    except Exception:
        pass

    logger.info("启动 scraper: %s", " ".join(args))

    # stdout+stderr 写入日志文件（追加模式保留历史；风格与 crawl.py _start_scraper 一致）
    log_path = Path(get_settings().DATA_DIR) / "scraper_auto_stderr.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a", encoding="utf-8")

    # 创建子进程：进程组隔离（支持整树杀）
    popen_kwargs: dict = {"env": env}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=log_file,
            stderr=log_file,
            **popen_kwargs,
        )
        # Phase 2 P1-2：注册全局爬取锁（原子获取+登记），防止与手动/单任务爬取并发互踩
        from services import scraper_lock
        if not scraper_lock.try_acquire_and_set(proc, {
            "mode": args[0] if args else "auto",
            "args": " ".join(args),
            "pid": proc.pid,
            "started_at": datetime.utcnow().isoformat(),
            "auto": True,
        }):
            # 锁被占用：回收刚启动的进程，避免残留 Chromium
            _kill_process_tree(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except Exception:
                pass
            logger.warning("全局爬取锁被占用，已终止本次自动 scraper: %s", " ".join(args))
            return False
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
            if proc.returncode == 0:
                logger.info("scraper 完成: %s", " ".join(args))
                return True
            _state["errors"] += 1
            logger.error(
                "scraper 退出码 %d（累计错误 %d），stderr 见 %s: %s",
                proc.returncode, _state["errors"], log_path, " ".join(args),
            )
            return False
        except asyncio.TimeoutError:
            # 先给 scraper 优雅退出机会（flush/清理浏览器会话），等 10s 未退出再整树强杀
            await _graceful_terminate(proc, grace=10.0)
            _kill_process_tree(proc)
            logger.warning(
                "scraper 超时(%ds)已尝试优雅退出，随后 kill 整树: %s",
                timeout, " ".join(args),
            )
            return False
    except Exception as e:
        logger.error("scraper 执行异常: %s", e)
        return False
    finally:
        # Phase 2 P1-3：按身份释放锁（get_proc() is proc 才 clear，防 ABA）
        if proc is not None:
            from services import scraper_lock
            if scraper_lock.get_proc() is proc:
                scraper_lock.clear()
        log_file.close()


def _kill_process_tree(proc) -> None:
    """杀整个进程树（包括 Playwright Chromium 子进程）。"""
    if proc.returncode is not None:
        return  # 已退出
    pid = proc.pid
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


async def _graceful_terminate(proc, grace: float = 10.0) -> None:
    """超时后先尝试优雅退出：Unix 发 SIGTERM 到进程组，Windows 发 CTRL_BREAK_EVENT。

    等 grace 秒让进程自行清理；未退出则由调用方继续 _kill_process_tree 强杀兜底。
    """
    if proc.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            # CREATE_NEW_PROCESS_GROUP 启动的进程组可收 CTRL_BREAK_EVENT
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace)
    except Exception:
        pass


async def run_scan_cycle() -> dict:
    """对所有列表源执行一轮 scan。"""
    if _state["running"]:
        logger.warning("已有爬取在运行，跳过本次")
        return {"ok": False, "message": "已在运行"}
    # 检查全局锁：手动触发的 scraper 在跑则跳过（避免并发互踩浏览器）
    from services import scraper_lock
    if scraper_lock.is_running():
        logger.warning("手动爬取进行中，跳过自动 scan")
        return {"ok": False, "message": "手动爬取进行中"}
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
                ["scan", "--list-source-id", str(src.id), "-p", str(src.max_pages or 100)],
                timeout=_BATCH_TIMEOUT,  # 串行扫全部源，一轮可能远超 30 分钟
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
    from services import scraper_lock
    if scraper_lock.is_running():
        logger.warning("手动爬取进行中，跳过自动 extract")
        return {"ok": False, "message": "手动爬取进行中"}
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
            ok = await _run_scraper(
                ["extract", "--list-source-id", str(src.id), "--failed-only"],
                timeout=_BATCH_TIMEOUT,  # 单源 extract 100+ 任务约 2.5-3.5h，见模块常量注释
            )
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
