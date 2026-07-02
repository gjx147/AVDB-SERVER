"""爬取控制路由 —— 触发 scan/extract（subprocess 调 scraper）+ 状态查询。

设计：scraper 作为独立子进程运行，通过 crawl_status.json 文件传递实时进度，
通过 register/unregister HTTP 回调报告进程级状态。
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


@router.post("/scan")
def start_scan(req: CrawlRequest, _user: CurrentUser):
    """启动扫描（subprocess 调 scraper.py scan）。"""
    global _running_proc, _running_info
    if _running_proc and _running_proc.poll() is None:
        raise HTTPException(status_code=409, detail="已有爬取任务在运行")

    cmd = [_python_exe(), str(_scraper_path()), "scan",
           "--list-source-id", str(req.list_source_id)]
    if req.pages:
        cmd += ["-p", str(req.pages)]

    env = dict(os.environ)
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    _running_proc = proc
    _running_info = {"list_source_id": req.list_source_id, "mode": "scan", "pid": proc.pid}
    return {"ok": True, "pid": proc.pid, "mode": "scan"}


@router.post("/extract")
def start_extract(req: CrawlRequest, _user: CurrentUser):
    """启动提取（subprocess 调 scraper.py extract）。"""
    global _running_proc, _running_info
    if _running_proc and _running_proc.poll() is None:
        raise HTTPException(status_code=409, detail="已有爬取任务在运行")

    cmd = [_python_exe(), str(_scraper_path()), "extract",
           "--list-source-id", str(req.list_source_id)]
    if req.limit:
        cmd += ["--limit", str(req.limit)]
    if req.failed_only:
        cmd += ["--failed-only"]

    env = dict(os.environ)
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    _running_proc = proc
    _running_info = {"list_source_id": req.list_source_id, "mode": "extract", "pid": proc.pid}
    return {"ok": True, "pid": proc.pid, "mode": "extract"}


@router.get("/status")
def crawl_status(_user: CurrentUser):
    """查询爬取状态：进程级（内存）+ 任务级（crawl_status.json）。"""
    proc_running = _running_proc is not None and _running_proc.poll() is None

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
    """停止当前爬取进程。"""
    global _running_proc, _running_info
    if _running_proc and _running_proc.poll() is None:
        _running_proc.terminate()
        try:
            _running_proc.wait(timeout=10)
        except Exception:
            _running_proc.kill()
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
    global _running_info
    _running_info = {}
    return {"ok": True}
