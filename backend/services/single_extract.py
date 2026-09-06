"""单 URL 详情提取子进程启动器。

从 tasks.py extract_single 模板抽出（回调 token / 代理注入 / 进程组隔离 / 超时整树杀），
供演员手动添加作品等新端点复用。
"""
import os
import subprocess
import sys
from datetime import datetime

from config import get_settings


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """杀整个进程树（包括 Playwright Chromium 子进程）。对齐 tasks.py._kill_process_tree。"""
    from services import scraper_lock
    if not scraper_lock.is_proc_alive(proc):
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10,
            )
        else:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _reap_and_clear(proc: subprocess.Popen, timeout: int = 1800) -> None:
    """后台线程：等待子进程；超时整树杀；按身份释放全局锁（防 ABA）。"""
    from services import scraper_lock
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
    except Exception:
        pass
    finally:
        scraper_lock.clear_if_current(proc)  # 原子按身份释放（防 ABA 窗口误清新进程）


def _build_env() -> dict:
    """子进程 env：注入回调共享密钥 + DB 里的代理/镜像站设置（否则无代理被 Cloudflare 拦截）。"""
    from services import scraper_lock
    from database import SessionLocal
    from models import Setting
    _env = dict(os.environ)
    _env["SCRAPER_CALLBACK_TOKEN"] = scraper_lock.get_callback_token()
    db = SessionLocal()
    try:
        _row = db.get(Setting, "http_proxy")
        if _row and _row.value:
            _val = _row.value.strip()
            _env["HTTP_PROXY"] = _val
            _env["HTTPS_PROXY"] = _val
            _env["http_proxy"] = _val
            _env["https_proxy"] = _val
        _row = db.get(Setting, "javdb_url")
        if _row and _row.value:
            _env["JAVDB_URL"] = _row.value.strip()
    finally:
        db.close()
    return _env


def spawn_extract_single(url: str, actor_id: int | None = None,
                         ensure_task: bool = False, list_source_id: int | None = None) -> dict:
    """拉起 extract-single 子进程（fire-and-forget）。返回 {ok, message}。

    - 原子获取+注册全局锁；被占用则回收刚启动的进程并返回 ok=False（调用方自行决定 UX）；
    - 后台线程 30 分钟超时整树杀 + 按身份释放锁。
    """
    import threading
    from services import scraper_lock
    settings = get_settings()
    scraper = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                           "magnet_scraper", "scraper.py")
    python = settings.SCRAPER_PYTHON or sys.executable
    try:
        cmd = [python, scraper, "extract-single", "--url", url]
        if actor_id is not None:
            cmd += ["--actor-id", str(actor_id)]
        if ensure_task:
            cmd += ["--ensure-task"]
            if list_source_id is not None:
                cmd += ["--list-source-id", str(list_source_id)]

        popen_kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "env": _build_env(),
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(cmd, **popen_kwargs)

        if not scraper_lock.try_acquire_and_set(proc, {
            "mode": "extract-single", "pid": proc.pid, "task_url": url,
            "started_at": datetime.utcnow().isoformat(),
        }):
            _kill_process_tree(proc)
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            return {"ok": False, "message": "已有爬取任务在运行"}

        threading.Thread(target=_reap_and_clear, args=(proc,), daemon=True).start()
        return {"ok": True, "message": "已触发提取"}
    except Exception as e:
        return {"ok": False, "message": str(e)}
