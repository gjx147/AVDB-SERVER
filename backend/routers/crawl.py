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

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from config import get_settings
from deps import CurrentUser, DbSession
from sqlalchemy import select

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
    - stdout+stderr 重定向到日志文件（调试用，不 PIPE 避免死锁）
    - start_new_session=True（Unix 进程组，支持整组 kill 杀 Chromium 子树）
    - Windows 用 CREATE_NEW_PROCESS_GROUP
    """
    settings = get_settings()
    env = dict(os.environ)
    cmd = [_python_exe(), str(_scraper_path())] + cmd_args

    # stdout+stderr 写入日志文件（覆盖模式，每次爬取只保留最新一次的日志）
    log_path = Path(settings.DATA_DIR) / "scraper_stderr.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "w", encoding="utf-8")

    popen_kwargs: dict = {
        "env": env,
        "stdout": log_file,  # stdout 写文件（scraper 日志全部可见）
        "stderr": subprocess.STDOUT,  # stderr 合并到 stdout
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
        "paused": False,
        "process": _running_info if proc_running else None,
        "task": task_status,
        # 兼容前端 CrawlStatus 类型
        "list_code": _running_info.get("list_code") if proc_running else (task_status.get("list_code") if task_status else None),
        "crawl_type": _running_info.get("mode") if proc_running else (task_status.get("crawl_type") if task_status else None),
        "progress": task_status,
    }


@router.post("/pause")
def pause_crawl(_user: CurrentUser):
    """暂停（当前实现等同 stop，因为 scraper 子进程不支持暂停）。"""
    return stop_crawl(_user)


@router.post("/resume")
def resume_crawl(_user: CurrentUser):
    """恢复（需重新触发 scan/extract）。"""
    return {"ok": True, "message": "请重新触发 scan 或 extract"}


@router.post("/extract-failed")
def extract_failed(req: CrawlRequest, _user: CurrentUser):
    """重试失败任务（兼容前端，转调 extract --failed-only）。"""
    req.failed_only = True
    return start_extract(req, _user)


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


# ── Phase 1 补端点：日志查询 ──

@router.post("/ranking")
def crawl_ranking(body: dict, _user: CurrentUser):
    """触发排行榜爬取（兼容前端 POST /api/crawl/ranking）。

    前端传 {rank_type, max_pages}，后端启动 scraper ranking 子命令。
    """
    global _running_proc, _running_info
    if _running_proc and _running_proc.poll() is None:
        raise HTTPException(status_code=409, detail="已有爬取任务在运行")

    rank_type = body.get("rank_type", "hot")
    valid_types = {"hot", "weekly", "monthly", "daily"}
    if rank_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"无效 rank_type，可选: {valid_types}")
    max_pages = str(body.get("max_pages", 5))
    cmd = ["ranking", "--rank-type", rank_type, "--max-pages", max_pages]

    proc = _start_scraper(cmd)
    _running_proc = proc
    _running_info = {
        "mode": "ranking", "rank_type": rank_type, "pid": proc.pid,
        "started_at": _now_iso(),
    }
    return {"ok": True, "pid": proc.pid, "mode": "ranking"}


@router.post("/actor")
def crawl_actor(body: dict, _user: CurrentUser):
    """触发演员爬取（兼容前端 POST /api/crawl/actor）。"""
    global _running_proc, _running_info
    if _running_proc and _running_proc.poll() is None:
        raise HTTPException(status_code=409, detail="已有爬取任务在运行")

    actor_url = body.get("actor_url", "")
    cmd = ["crawl-actor", "--actor-url", actor_url]

    proc = _start_scraper(cmd)
    _running_proc = proc
    _running_info = {
        "mode": "actor", "actor_url": actor_url, "pid": proc.pid,
        "started_at": _now_iso(),
    }
    return {"ok": True, "pid": proc.pid, "mode": "actor"}


@router.post("/actor-search")
def actor_search(body: dict, _user: CurrentUser):
    """搜索演员（通过 scraper 子进程的 Playwright 执行）。

    后端 browser_pool 与 scraper 子进程共用浏览器有冲突风险，
    因此演员搜索需通过 crawl-actor 子命令完成。
    这里返回提示信息，前端可引导用户直接输入演员 URL。
    """
    name = body.get("actor_name", "") or body.get("q", "")
    if not name:
        raise HTTPException(status_code=400, detail="需要演员名")
    return {
        "ok": True,
        "results": [],
        "message": f"请在演员库页面直接输入演员详情页 URL，或通过 crawl-actor --actor-name '{name}' 子进程搜索",
    }


@router.get("/logs")
def crawl_logs(
    db: DbSession,
    _user: CurrentUser,
    limit: int = Query(100, ge=1, le=500),
):
    """爬取日志列表（兼容 AVDB 前端）。"""
    from models import CrawlLog
    logs = db.execute(
        select(CrawlLog).order_by(CrawlLog.created_at.desc()).limit(limit)
    ).scalars().all()
    return {
        "lines": [
            f"[{l.level}] {l.message}"
            for l in logs
        ],
        "running": _running_proc is not None and _running_proc.poll() is None,
    }


@router.get("/stderr")
def crawl_stderr(_user: CurrentUser, limit: int = Query(100, ge=1, le=500)):
    """读取 scraper 子进程的 stderr 日志（调试崩溃用）。"""
    settings = get_settings()
    log_path = Path(settings.DATA_DIR) / "scraper_stderr.log"
    if not log_path.exists():
        return {"lines": [], "exists": False}
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        all_lines = text.strip().split("\n") if text.strip() else []
        return {"lines": all_lines[-limit:], "exists": True, "total": len(all_lines)}
    except Exception as e:
        return {"lines": [], "exists": True, "error": str(e)}
