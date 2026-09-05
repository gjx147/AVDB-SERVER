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
import secrets
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from config import get_settings
from deps import CurrentUser, DbSession
from sqlalchemy import select
from services import scraper_lock

router = APIRouter(prefix="/api/crawl", tags=["crawl"])

# 默认超时（30 分钟）
# 爬虫硬超时（秒）：watchdog 超过该时长强杀进程。
# 可用环境变量 SCRAPER_TIMEOUT_S 覆盖；默认 43200（12 小时）。
# 依据：实测每任务约 38 秒，995 任务的演员约需 10.5 小时——
# 12 小时上限留足余量；更大的演员请按需调大该环境变量。
# 历史 bug：曾固定 1800（30 分钟）——演员全量补齐动辄数千任务需 10h+，
# 30 分钟必被 watchdog 杀掉，表现为"补齐自己停止、日志无报错"。
_DEFAULT_TIMEOUT = int(os.environ.get("SCRAPER_TIMEOUT_S", "43200"))

# Phase 2 F07：scraper 回调共享密钥已移至 services.scraper_lock
# （rotate_callback_token / get_callback_token），所有触发路径共用。


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


def _live_state_file() -> Path:
    """爬取上下文落盘（容器意外重启后自恢复用）。

    - _start_scraper_guarded 成功时写入 {cmd_args, info, started_at}
    - 子进程正常收尾/手动停止时删除
    - 启动时残留 = 上次爬取被异常打断（容器重启/OOM）→ 自动续爬
    """
    return Path("data") / "scraper_live.json"


def _crawl_status_file() -> Path:
    settings = get_settings()
    return Path(settings.DATA_DIR) / "crawl_status.json"


def _get_proxy_from_db() -> str:
    """从 DB settings 表读取运行时配置的代理地址。"""
    return _get_setting_from_db("http_proxy")


def _get_setting_from_db(key: str) -> str:
    """从 DB settings 表读取运行时配置。"""
    try:
        from database import SessionLocal
        from models import Setting
        db = SessionLocal()
        try:
            row = db.get(Setting, key)
            return row.value.strip() if row and row.value else ""
        finally:
            db.close()
    except Exception:
        return ""


def _start_scraper(cmd_args: list[str], channel: str = "main") -> subprocess.Popen:
    """启动 scraper 子进程。

    架构修复：
    - stdout+stderr 重定向到日志文件（调试用，不 PIPE 避免死锁）
    - 从 DB settings 读 http_proxy，注入子进程 env（覆盖启动时环境变量）
    - start_new_session=True（Unix 进程组，支持整组 kill 杀 Chromium 子树）
    - Windows 用 CREATE_NEW_PROCESS_GROUP
    """
    settings = get_settings()
    env = dict(os.environ)

    # Phase 2 F07：轮换回调共享密钥，注入子进程 env
    # （register/unregister 端点校验 Authorization: Bearer <token>）
    env["SCRAPER_CALLBACK_TOKEN"] = scraper_lock.rotate_callback_token()

    # 双通道：top250 通道用独立浏览器配置目录（cookie 由登录流程/启动引导同步）
    if channel == scraper_lock.CHANNEL_TOP250:
        env["SCRAPER_PROFILE"] = "browser_profile_top250"

    # 从 DB 读运行时代理配置，覆盖 env（让 scraper 子进程的 Playwright 生效）
    proxy = _get_proxy_from_db()
    if proxy:
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy
        env["http_proxy"] = proxy
        env["https_proxy"] = proxy

    # 从 DB 读 javdb_url，覆盖 env（让 scraper 访问自定义镜像站）
    javdb_url = _get_setting_from_db("javdb_url")
    if javdb_url:
        env["JAVDB_URL"] = javdb_url

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
    if not scraper_lock.is_proc_alive(proc):
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


def _write_live_state(cmd_args: list[str], info: dict) -> None:
    """写入爬取上下文（供重启自恢复）。"""
    import json
    try:
        f = _live_state_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({
            "cmd_args": [str(a) for a in cmd_args],
            "info": {k: v for k, v in info.items() if isinstance(v, (str, int, float, bool)) or v is None},
            "started_at": _now_iso(),
            "pid": info.get("pid"),
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _clear_live_state() -> None:
    """正常收尾/手动停止时清除爬取上下文。"""
    try:
        f = _live_state_file()
        if f.exists():
            f.unlink()
    except Exception:
        pass


def recover_interrupted_scraper() -> dict:
    """启动自恢复：检测上次异常中断（容器重启/OOM/被杀）的爬取并自动续爬。

    返回 {"recovered": bool, "message": str}。供 lifespan 启动时调用。
    """
    import json as _json
    f = _live_state_file()
    if not f.exists():
        return {"recovered": False, "message": "无中断爬取记录"}
    try:
        state = _json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"recovered": False, "message": "中断记录损坏，跳过"}
    cmd_args = state.get("cmd_args") or []
    if not cmd_args:
        return {"recovered": False, "message": "中断记录无命令，跳过"}
    mode = cmd_args[1] if len(cmd_args) > 1 else "?"
    f.unlink(missing_ok=True)
    if scraper_lock.is_running():
        return {"recovered": False, "message": f"已有爬取在运行，跳过恢复 {mode}"}
    try:
        _ch = (state.get("info") or {}).get("channel") or "main"
        proc = _start_scraper_guarded(cmd_args, state.get("info") or {}, channel=_ch)
        return {"recovered": True, "message": f"已自动续爬 {mode}（PID {proc.pid}）"}
    except Exception as e:
        return {"recovered": False, "message": f"自动续爬失败: {e}"}


def _start_scraper_guarded(cmd_args: list[str], info: dict, channel: str = "main") -> subprocess.Popen:
    """原子启动 scraper 并注册全局锁（Phase 2 P1-1 TOCTOU 修复）。

    旧实现 try_acquire() → Popen → set_proc() 之间存在竞态窗口：
    两个并发请求可同时通过 try_acquire()，各自启动一个 Playwright Chromium。
    新实现先 Popen（非阻塞，立即返回），再调用 scraper_lock.try_acquire_and_set()
    在锁内原子完成“检查是否已有活跳进程 + 登记 proc/info”；
    若锁被占用则立即杀掉刚启动的进程树（避免残留 Chromium）并抛 409。
    """
    from routers.javdb_login import is_active as _login_active
    if _login_active():
        raise HTTPException(status_code=409, detail="JavDB 登录会话进行中，请完成或取消后再启动爬取")
    if channel == scraper_lock.CHANNEL_TOP250:
        from routers.javdb_login import ensure_top250_profile
        ensure_top250_profile()
    proc = _start_scraper(cmd_args, channel)
    if not scraper_lock.try_acquire_and_set(proc, {**info, "pid": proc.pid, "channel": channel}, channel=channel):
        # 锁被占用：回收刚启动的进程，避免孤儿 Chromium 残留
        _kill_process_tree(proc)
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        raise HTTPException(status_code=409, detail="已有爬取任务在运行")
    _write_live_state(cmd_args, info)
    return proc


@router.post("/scan")
def start_scan(req: CrawlRequest, _user: CurrentUser):
    """启动扫描（subprocess 调 scraper.py scan）。"""
    cmd = ["scan", "--list-source-id", str(req.list_source_id)]
    if req.pages:
        cmd += ["-p", str(req.pages)]

    proc = _start_scraper_guarded(cmd, {
        "list_source_id": req.list_source_id, "mode": "scan",
        "started_at": _now_iso(),
    })
    return {"ok": True, "pid": proc.pid, "mode": "scan"}


@router.post("/extract")
def start_extract(req: CrawlRequest, _user: CurrentUser):
    """启动提取（subprocess 调 scraper.py extract）。"""
    cmd = ["extract", "--list-source-id", str(req.list_source_id)]
    if req.limit:
        cmd += ["--limit", str(req.limit)]
    if req.failed_only:
        cmd += ["--failed-only"]

    proc = _start_scraper_guarded(cmd, {
        "list_source_id": req.list_source_id, "mode": "extract",
        "started_at": _now_iso(),
    })
    return {"ok": True, "pid": proc.pid, "mode": "extract"}


@router.get("/logs")
def get_logs(file: str, lines: int, _user: CurrentUser):
    """爬取控制台日志查看器：读 data/ 下白名单日志文件尾部。"""
    from services.logging_config import LOG_FILES
    if file not in LOG_FILES:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="非法日志文件")
    from pathlib import Path
    from config import get_settings
    log_path = Path(get_settings().DATA_DIR) / file
    items: list[str] = []
    if log_path.exists():
        raw = log_path.read_bytes()
        text = None
        for enc in ("utf-8", "gbk"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw.decode("utf-8", errors="replace")
        all_lines = [ln for ln in text.strip().split("\n") if ln.strip()]
        items = all_lines[-min(max(lines, 10), 500):]
    return {"ok": True, "file": file, "name": LOG_FILES[file], "items": items}


@router.get("/log-files")
def list_log_files(_user: CurrentUser):
    """可查看的日志文件清单。"""
    from services.logging_config import LOG_FILES
    from pathlib import Path
    from config import get_settings
    data_dir = Path(get_settings().DATA_DIR)
    items = []
    for fname, name in LOG_FILES.items():
        p2 = data_dir / fname
        items.append({"file": fname, "name": name,
                      "exists": p2.exists(),
                      "size": p2.stat().st_size if p2.exists() else 0,
                      "mtime": p2.stat().st_mtime if p2.exists() else None})
    return {"ok": True, "items": items}


@router.get("/health")
def crawl_health(db: DbSession, _user: CurrentUser):
    """爬虫健康概览（F3）：24h 成功率、失败原因归类、7 日趋势。"""
    from datetime import datetime, timedelta
    from models import CrawlLog

    now = datetime.utcnow()
    since = now - timedelta(hours=24)
    logs = db.execute(
        select(CrawlLog).where(CrawlLog.created_at >= since)
    ).scalars().all()
    total = len(logs)
    errors = [l for l in logs if l.level in ("error", "warn")]
    reasons = {"cf_challenge": 0, "timeout": 0, "parse_error": 0, "proxy_error": 0, "other": 0}
    for l in errors:
        m = (l.message or "").lower()
        if "cloudflare" in m or "blocked" in m or "拦截" in m:
            reasons["cf_challenge"] += 1
        elif "timeout" in m or "超时" in m:
            reasons["timeout"] += 1
        elif "parse" in m or "解析" in m:
            reasons["parse_error"] += 1
        elif "proxy" in m or "代理" in m:
            reasons["proxy_error"] += 1
        else:
            reasons["other"] += 1

    # 7 日趋势（单次查询内存分桶）
    week_logs = db.execute(
        select(CrawlLog.created_at, CrawlLog.level).where(CrawlLog.created_at >= now - timedelta(days=7))
    ).all()
    trend = []
    for i in range(6, -1, -1):
        day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        day_logs = [l for l in week_logs if day_start <= l[0] < day_end]
        trend.append({
            "date": day_start.strftime("%m-%d"),
            "total": len(day_logs),
            "errors": len([l for l in day_logs if l[1] in ("error", "warn")]),
        })
    return {
        "total_24h": total,
        "error_24h": len(errors),
        "success_rate": round((total - len(errors)) / total * 100, 1) if total else 100.0,
        "reasons": reasons,
        "trend": trend,
    }


@router.get("/diagnostics")
def crawl_diagnostics(db: DbSession, _user: CurrentUser):
    """依赖连通性检测（F3）：代理与目标站可达性。"""
    import httpx
    from models import Setting

    def _get(key: str) -> str:
        row = db.get(Setting, key)
        return row.value if row else ""

    proxy = _get("http_proxy") or ""
    javdb = _get("javdb_url") or "https://javdb.com"
    result = {"proxy": "skip", "javdb": "fail", "javdb_url": javdb}

    # 代理连通性（经代理请求 204 端点）
    if proxy:
        try:
            r = httpx.get("https://www.google.com/generate_204", proxy=proxy, timeout=8)
            result["proxy"] = "ok" if r.status_code == 204 else "fail"
        except Exception:
            result["proxy"] = "fail"
    # 目标站可达性
    try:
        r = httpx.get(javdb, timeout=10, follow_redirects=True, verify=False)
        result["javdb"] = "ok" if r.status_code < 500 else "fail"
    except Exception:
        result["javdb"] = "fail"
    return result


@router.get("/status")
def crawl_status(_user: CurrentUser):
    """查询爬取状态：进程级（内存）+ 任务级（crawl_status.json）。"""
    # 超时检查 + 僵尸进程回收（懒触发；另有 scheduler watchdog 每 60s 主动兜底）
    reap_result = reap_timed_out_crawl()
    proc_running = reap_result["running"]
    info = scraper_lock.get_info()

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
        "process": info if proc_running else None,
        "task": task_status,
        # 兼容前端 CrawlStatus 类型
        "list_code": info.get("list_code") if proc_running else (task_status.get("list_code") if task_status else None),
        "crawl_type": info.get("mode") if proc_running else (task_status.get("crawl_type") if task_status else None),
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


@router.post("/refresh-actor-gender")
def refresh_actor_gender(req: CrawlRequest, _user: CurrentUser):
    """重新爬取已访问 task，刷新 actors（女优优先）+ tags（去 navbar 污染）+ actors.gender。

    修正老数据：旧版 _extract_actors 把 navbar 的 Censored/Uncensored/Western 当演员，
    且 actors.gender 全 null。新版用 javdb ♀/♂ 标记正确识别女优。
    """
    cmd = ["refresh-actor-gender"]
    if req.limit:
        cmd += ["--limit", str(req.limit)]

    proc = _start_scraper_guarded(cmd, {
        "list_source_id": req.list_source_id, "mode": "refresh-actor-gender",
        "started_at": _now_iso(),
    })
    return {"ok": True, "pid": proc.pid, "mode": "refresh-actor-gender"}


@router.post("/refresh-metadata")
def refresh_metadata(_user: CurrentUser, body: dict | None = None):
    """刷新已访问任务的元数据面板（发行日期/评分/厂牌/系列等），不重抓磁力。"""
    cmd = ["refresh-metadata"]
    limit = (body or {}).get("limit")
    if limit:
        cmd += ["--limit", str(limit)]

    proc = _start_scraper_guarded(cmd, {
        "mode": "refresh-metadata",
        "started_at": _now_iso(),
    })
    return {"ok": True, "pid": proc.pid, "mode": "refresh-metadata"}


@router.post("/stop")
def stop_crawl(_user: CurrentUser):
    """停止当前爬取进程（杀整个进程树；双通道全停）。"""
    for _ch in (scraper_lock.CHANNEL_MAIN, scraper_lock.CHANNEL_TOP250):
        proc = scraper_lock.get_proc(_ch)
        if proc and scraper_lock.is_proc_alive(proc):
            _kill_process_tree(proc)
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
        # Phase 2 P1-3：按身份释放（防止清掉并发新注册的锁）
        if proc is not None and scraper_lock.get_proc(_ch) is proc:
            scraper_lock.clear(_ch)
    return {"ok": True, "message": "已停止"}


# scraper 回调端点（register/unregister）—— Phase 2 F07：校验共享密钥。
# 子进程启动时由 _start_scraper 注入 SCRAPER_CALLBACK_TOKEN env；
# 未配置 token（如端点被直接调用）一律拒绝（fail closed）。
def _verify_callback_token(authorization: str | None) -> bool:
    if not authorization or not authorization.startswith("Bearer "):
        return False
    token = authorization[len("Bearer "):].strip()
    _expected = scraper_lock.get_callback_token()
    return bool(_expected) and secrets.compare_digest(token, _expected)


@router.post("/register")
def register(body: dict, authorization: str | None = Header(None)):
    if not _verify_callback_token(authorization):
        raise HTTPException(status_code=401, detail="未授权")
    # 双通道：按回调 pid 定位登记通道（scraper 子进程上报 os.getpid()）
    ch = scraper_lock.find_channel_by_pid(body.get("pid")) or scraper_lock.CHANNEL_MAIN
    info = scraper_lock.get_info(ch)
    scraper_lock.set_proc(scraper_lock.get_proc(ch), {**info, **body, "registered": True}, channel=ch)
    return {"ok": True}


@router.post("/unregister")
def unregister(authorization: str | None = Header(None)):
    if not _verify_callback_token(authorization):
        raise HTTPException(status_code=401, detail="未授权")
    # 双通道：回调无 body，按"已登记且进程已退出"清扫对应通道
    for _ch in (scraper_lock.CHANNEL_MAIN, scraper_lock.CHANNEL_TOP250):
        _proc = scraper_lock.get_proc(_ch)
        if _proc and not scraper_lock.is_proc_alive(_proc):
            scraper_lock.clear(_ch)
            _clear_live_state()
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


def reap_timed_out_crawl() -> dict:
    """主动回收超时/僵死的爬取进程（含进程已退出但锁未清理的情况）。

    供 crawl_status（懒触发）与 scheduler 的 watchdog 定时任务共用，
    保证前端不轮询时僵尸进程也能被回收。
    幂等：每次重查 poll()，且按身份（get_proc() is proc）clear，防 ABA。
    """
    running = False
    reaped = False
    for _ch in (scraper_lock.CHANNEL_MAIN, scraper_lock.CHANNEL_TOP250):
        proc = scraper_lock.get_proc(_ch)
        if proc is None:
            continue
        if scraper_lock.is_proc_alive(proc):
            running = True
            if _ch == scraper_lock.CHANNEL_MAIN and _is_timed_out(scraper_lock.get_info(_ch)):
                _kill_process_tree(proc)  # type: ignore
                _clear_live_state()
                reaped = True
                if scraper_lock.get_proc(_ch) is proc:
                    scraper_lock.clear(_ch)
                running = False
        else:
            # 进程已退出但锁未清理（僵尸锁）
            if scraper_lock.get_proc(_ch) is proc:
                scraper_lock.clear(_ch)
    return {"running": running, "reaped": reaped}


# ── Phase 1 补端点：日志查询 ──

@router.post("/ranking")
def crawl_ranking(body: dict, _user: CurrentUser):
    """触发排行榜爬取（兼容前端 POST /api/crawl/ranking）。

    前端传 {rank_type, max_pages}，后端启动 scraper ranking 子命令。
    """
    rank_type = body.get("rank_type", "hot")
    valid_types = {"daily", "weekly", "monthly", "actor"}
    if rank_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"无效 rank_type，可选: {valid_types}")
    max_pages = str(body.get("max_pages", 5))
    cmd = ["ranking", "--rank-type", rank_type, "--max-pages", max_pages]

    proc = _start_scraper_guarded(cmd, {
        "mode": "ranking", "rank_type": rank_type,
        "started_at": _now_iso(),
    })
    return {"ok": True, "pid": proc.pid, "mode": "ranking"}


def start_actor_crawl(actor_url: str, actor_id: int | None = None, max_co_star: int | None = None,
                      solo_only: bool = False, actor_name: str | None = None,
                      video_filter: str = "none", exclude_vr: bool = False,
                      since: str = "") -> dict:
    """公共函数：触发演员作品爬取子进程（供 /api/crawl/actor 和 /api/actors/{id}/crawl-works 复用）。

    检查全局进程锁 → 启动 crawl-actor 子进程 → 记录运行状态。
    actor_id：已知目标演员时传入，scraper 会按 id 关联作品，杜绝因名字匹配建重复演员。
    max_co_star：最大共演人数限制（作品女演员数超过则跳过；None/0 = 不限）。
    solo_only：只爬单体作品（javdb 演员页 t=s 过滤）——向后兼容，等价 video_filter="solo"。
    video_filter：作品列表过滤（需登录）solo/magnet/subtitle/none。
    exclude_vr：排除 VR 作品（演员页 VR 标签集合差；需登录）。
    """
    if actor_name and not actor_url:
        # 无 URL：按演员名搜索源站（搜索到自动回写 source_url）
        cmd = ["crawl-actor", "--actor-name", actor_name]
    else:
        cmd = ["crawl-actor", "--actor-url", actor_url]
    if actor_id is not None:
        cmd += ["--actor-id", str(actor_id)]
    if max_co_star and max_co_star > 0:
        cmd += ["--max-co-star", str(max_co_star)]
    if solo_only and video_filter == "none":
        video_filter = "solo"
    if video_filter and video_filter != "none":
        cmd += ["--video-filter", video_filter]
    if exclude_vr:
        cmd += ["--exclude-vr"]
    if since:
        cmd += ["--since", since]
    proc = _start_scraper_guarded(cmd, {
        "mode": "actor", "actor_url": actor_url or f"search:{actor_name}",
        "started_at": _now_iso(),
    })
    return {"ok": True, "pid": proc.pid, "mode": "actor", "actor_url": actor_url or f"search:{actor_name}"}


@router.post("/actor")
def crawl_actor(body: dict, _user: CurrentUser):
    """触发演员爬取（兼容前端 POST /api/crawl/actor）。"""
    actor_url = body.get("actor_url", "")
    return start_actor_crawl(actor_url)


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
        "running": scraper_lock.is_running(),
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
