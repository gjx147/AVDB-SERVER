"""爬取控制路由 —— 触发 scan/extract（subprocess 调 scraper）+ 状态查询。

设计：scraper 作为独立子进程运行，通过 crawl_status.json 文件传递实时进度，
通过 register/unregister HTTP 回调报告进程级状态。

架构修复（P0）：
- stdout 用 DEVNULL（不读 pipe，避免输出超 64KB 死锁）
- 进程组启动（start_new_session），stop 时整组 kill（杀 Chromium 子进程树）
- 超时回收（默认 30 分钟，防止僵尸进程永久占用 _running_proc）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import get_settings
from deps import CurrentUser

router = APIRouter(prefix="/api/crawl", tags=["crawl"])

# 进程级状态（内存中维护当前运行的 scraper 进程）
_running_proc: subprocess.Popen | None = None
_running_info: dict = {}

# 默认超时（30 分钟）
_DEFAULT_TIMEOUT = 1800


class CrawlRequest(BaseModel):
    list_source_id: int
    mode: str = "scan"  # scan / extract / auto
    pages: int | None = None
    limit: int | None = None
    failed_only: bool = False


def _scraper_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "magnet_scraper" / "scraper.py"


def _python_exe() -> str:
    settings = get_settings()
    return settings.SCRAPER_PYTHON or sys.executable


def _crawl_status_file() -> Path:
    settings = get_settings()
    return Path(settings.DATA_DIR) / "crawl_status.json"


def _start_scraper(cmd_args: list[str]) -> subprocess.Popen:
    """启动 scraper 子进程。

    架构修复：
    - stdout/stderr 用 DEVNULL（不读 pipe，避免死锁；进度走 crawl_status.json）
    - start_new_session=True（Unix 进程组，支持整组 kill 杀 Chromium 子树）
    - Windows 用 CREATE_NEW_PROCESS_GROUP
    """
    settings = get_settings()
    env = dict(os.environ)
    cmd = [_python_exe(), str(_scraper_path())] + cmd_args

    popen_kwargs: dict = {
        "env": env,
        "stdout": subprocess.DEVNULL,  # 关键：不 PIPE，避免死锁
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    return subprocess.Popen(cmd, **popen_kwargs)


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """杀整个进程树（包括 Chromium 子进程）。"""
    if proc.poll() is not None:
        return  # 已退出
    try:
        if sys.platform == "win32":
            # Windows: taskkill /T 杀整树
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10,
            )
        else:
            # Unix: 杀进程组（start_new_session 创建的）
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        # 兜底：直接 kill
        try:
            proc.kill()
        except Exception:
            pass


@router.post("/scan")
def start_scan(req: CrawlRequest, _user: CurrentUser):
    """启动扫描（subprocess 调 scraper.py scan）。"""
    global _running_proc, _running_info
    if _running_proc and _running_proc.poll() is None:
        raise HTTPException(status_code=409, detail="已有爬取任务在运行")

    cmd = ["scan", "--list-source-id", str(req.list_source_id)]
    if req.pages:
        cmd += ["-p", str(req.pages)]

    proc = _start_scraper(cmd)
    _running_proc = proc
    _running_info = {
        "list_source_id": req.list_source_id, "mode": "scan", "pid": proc.pid,
        "started_at": _now_iso(),
    }
    return {"ok": True, "pid": proc.pid, "mode": "scan"}


@router.post("/extract")
def start_extract(req: CrawlRequest, _user: CurrentUser):
    """启动提取（subprocess 调 scraper.py extract）。"""
    global _running_proc, _running_info
    if _running_proc and _running_proc.poll() is None:
        raise HTTPException(status_code=409, detail="已有爬取任务在运行")

    cmd = ["extract", "--list-source-id", str(req.list_source_id)]
    if req.limit:
        cmd += ["--limit", str(req.limit)]
    if req.failed_only:
        cmd += ["--failed-only"]

    proc = _start_scraper(cmd)
    _running_proc = proc
    _running_info = {
        "list_source_id": req.list_source_id, "mode": "extract", "pid": proc.pid,
        "started_at": _now_iso(),
    }
    return {"ok": True, "pid": proc.pid, "mode": "extract"}


@router.get("/status")
def crawl_status(_user: CurrentUser):
    """查询爬取状态：进程级（内存）+ 任务级（crawl_status.json）。"""
    global _running_proc
    proc_running = _running_proc is not None and _running_proc.poll() is None

    # 检查超时（清理僵尸进程）
    if proc_running and _is_timed_out(_running_info):
        _kill_process_tree(_running_proc)  # type: ignore
        _running_proc = None
        proc_running = False

    # 进程已退出但 _running_proc 未清理
    if _running_proc is not None and _running_proc.poll() is not None:
        _running_proc = None
        proc_running = False

    # 读任务级状态文件
    task_status = {}
    status_file = _crawl_status_file()
    if status_file.exists():
        try:
            task_status = json.loads(status_file.read_text(encoding="utf-8"))
        except Exception:
            task_status = {}

    return {
        "running": proc_running,
        "process": _running_info if proc_running else None,
        "task": task_status,
    }


@router.post("/stop")
def stop_crawl(_user: CurrentUser):
    """停止当前爬取进程（杀整个进程树）。"""
    global _running_proc, _running_info
    if _running_proc and _running_proc.poll() is None:
        _kill_process_tree(_running_proc)
        try:
            _running_proc.wait(timeout=10)
        except Exception:
            pass
    _running_proc = None
    _running_info = {}
    return {"ok": True, "message": "已停止"}


# scraper 回调端点（register/unregister，无需鉴权——子进程调用）
@router.post("/register")
def register(body: dict):
    global _running_info
    _running_info = {**_running_info, **body, "registered": True}
    return {"ok": True}


@router.post("/unregister")
def unregister():
    global _running_proc, _running_info
    _running_info = {}
    # 进程结束，清理引用
    if _running_proc and _running_proc.poll() is not None:
        _running_proc = None
    return {"ok": True}


def _now_iso() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat()


def _is_timed_out(info: dict) -> bool:
    """检查进程是否超时（默认 30 分钟）。"""
    started_at = info.get("started_at")
    if not started_at:
        return False
    from datetime import datetime, timedelta
    try:
        start = datetime.fromisoformat(started_at)
        return datetime.utcnow() - start > timedelta(seconds=_DEFAULT_TIMEOUT)
    except Exception:
        return False
