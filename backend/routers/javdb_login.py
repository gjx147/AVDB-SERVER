# -*- coding: utf-8 -*-
"""JavDB 人工辅助登录会话（干净版）：
- start: 开固定 profile 持久化浏览器停到 /login
- screenshot: 当前页截图 base64
- submit: 填账号/密码/验证码提交 → 复检
- status/cancel
与爬虫互斥（共用固定 profile）。
"""
import base64
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from deps import CurrentAdmin

router = APIRouter(prefix="/api/javdb-login", tags=["javdb-login"])

# 与爬虫 config.OUTPUT_DIR 同源：项目根/magnet_scraper/output/browser_profile
# backend cwd=/app/backend，必须基于 __file__ 定位（/app/backend/routers → 上三级 = /app）
PROFILE_DIR = Path(__file__).resolve().parent.parent.parent / "magnet_scraper" / "output" / "browser_profile"

_state = {"running": False, "logged_in": None, "message": "", "started_at": 0.0}
_lock = threading.Lock()
_ctx = {"pw": None, "ctx": None, "page": None, "stop": False,
        "shot": None,          # 会话线程自产的最新截图（data url），端点只读
        "cmd": None,           # 待处理命令 {"action": "submit", ...}（端点投递，线程消费）
        "submit_result": None}  # submit 处理结果（线程写，端点读）
TIMEOUT_S = 300
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"


def is_active() -> bool:
    return _state["running"]


def _db_path() -> Path:
    """与 backend/爬虫同源的 SQLite 路径。

    - DATABASE_URL 为 sqlite:/// 绝对路径时直接用
    - 空串（compose 默认）时回退项目根/data/javdb.db（= /app/data/javdb.db，
      与爬虫 config.DATA_DIR 同源；backend cwd=/app/backend，相对路径会错位）
    """
    import os
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):])
    return Path(__file__).resolve().parent.parent.parent / "data" / "javdb.db"


def _read_setting(key: str, default: str = "") -> str:
    try:
        import sqlite3
        db = _db_path()
        if db.exists():
            conn = sqlite3.connect(str(db), timeout=5)
            row = conn.execute("SELECT value FROM settings WHERE key=? LIMIT 1", (key,)).fetchone()
            conn.close()
            if row and row[0]:
                return str(row[0]).strip()
    except Exception:
        pass
    return default


def _cleanup() -> None:
    try:
        if _ctx["ctx"]:
            _ctx["ctx"].close()
        if _ctx["pw"]:
            _ctx["pw"].stop()
    except Exception:
        pass
    _ctx.update(pw=None, ctx=None, page=None)
    _state["running"] = False


def _proxy_server() -> tuple[str, str, str]:
    """返回 (server, username, password)。

    优先级：settings 表 http_proxy（用户配置，与爬虫实际生效一致）
    → 进程 env HTTP_PROXY/HTTPS_PROXY（compose 注入，兜底）。
    127.0.0.1/localhost 换 host.docker.internal（容器内网关）。
    user:pass@host 形式拆出鉴权（playwright proxy 参数要求）。
    """
    import os
    from urllib.parse import urlparse
    v = _read_setting("http_proxy")
    if not v:
        v = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or ""
    if not v:
        return "", "", ""
    if "127.0.0.1" in v or "localhost" in v:
        v = v.replace("127.0.0.1", "host.docker.internal").replace("localhost", "host.docker.internal")
    u = urlparse(v if "//" in v else "//" + v)
    server = f"{u.scheme or 'http'}://{u.hostname or 'host.docker.internal'}:{u.port or 80}"
    return server, u.username or "", u.password or ""


_GATE_SELECTORS = [
    "text=Yes, I am",  # 年龄确认（英文界面，绿色按钮 Yes, I am.）
    "text=是,我已滿18歲", "text=是,我已满18岁",  # 年龄确认（繁/简）
    "button:has-text('同意')", "a:has-text('同意')",  # 条款同意页
    "button:has-text('Agree')", "a:has-text('Agree')",
]


def _pass_gate(page) -> None:
    """自动点击同意页/年龄确认按钮（存在才点，点完等跳转）。"""
    try:
        for sel in _GATE_SELECTORS:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(2500)
            except Exception:
                continue
    except Exception:
        pass


def _session_thread() -> None:
    from playwright.sync_api import sync_playwright
    try:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        pw = sync_playwright().start()
        _ctx["pw"] = pw
        kwargs = dict(user_agent=UA, headless=True,
                      channel="chromium",  # Docker 镜像只装完整版 chromium（与爬虫 scraper.py 同参），headless_shell 变体不存在
                      args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        proxy, proxy_user, proxy_pass = _proxy_server()
        if proxy:
            pdict = {"server": proxy}
            if proxy_user:
                pdict["username"] = proxy_user
            if proxy_pass:
                pdict["password"] = proxy_pass
            kwargs["proxy"] = pdict
        ctx = pw.chromium.launch_persistent_context(str(PROFILE_DIR), **kwargs)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        _ctx["ctx"], _ctx["page"] = ctx, page
        login_url = f"{_read_setting('javdb_url', 'https://javdb.com').rstrip('/')}/login"
        last_err = None
        for attempt in range(2):
            try:
                page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
                last_err = None
                break
            except Exception as e:
                last_err = e
                _state["message"] = f"登录页加载超时（第 {attempt + 1} 次），重试中…"
        if last_err is not None:
            raise last_err
        page.wait_for_timeout(3000)
        # 新会话先弹同意页/年龄确认——自动点击（绿色按钮）
        _pass_gate(page)
        if "login" not in (page.url or ""):
            try:
                page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2000)
            except Exception:
                pass
        # 已登录检测：header 无 /login 链接 = 登录态有效（cookie 已在 profile）
        try:
            if page.locator("a[href='/login']").first.count() == 0:
                _state["logged_in"] = True
                _state["message"] = "当前已是登录状态，无需重复登录（可直接跑单体补齐走快速通道）"
        except Exception:
            pass
        _state["message"] = _state["message"] or "登录页已打开，看截图填账号、密码、验证码后提交"
        deadline = time.time() + TIMEOUT_S
        while time.time() < deadline and not _ctx["stop"] and _state["logged_in"] is not True:
            # 兜底过门（同意/年龄页可能延迟出现）
            _pass_gate(page)
            # 截图缓存（本线程内调用 page，跨线程安全）
            try:
                png = page.screenshot(type="png")
                _ctx["shot"] = "data:image/png;base64," + base64.b64encode(png).decode()
            except Exception:
                pass
            # 消费命令（submit / cancel）
            cmd = _ctx.get("cmd")
            if cmd:
                _ctx["cmd"] = None
                if cmd.get("action") == "cancel":
                    _ctx["stop"] = True
                    break
                if cmd.get("action") == "submit":
                    _ctx["submit_result"] = _do_submit(page, cmd)
            time.sleep(2)
        if _state["logged_in"] is True:
            time.sleep(2)  # cookie 落盘
            # 导出 storage_state（cookie+localStorage）供爬虫进程加载（跨进程登录态桥接）
            try:
                auth_path = PROFILE_DIR.parent / "javdb_auth.json"
                _ctx["ctx"].storage_state(path=str(auth_path))
                _state["message"] = "登录成功，cookie 已保存并同步给爬虫"
            except Exception as e:
                _state["message"] = f"登录成功，但 storage_state 导出失败: {e}"
            # 双通道：登录成功后把 cookie 同步到 top250 通道 profile
            _sync_top250_profile()
        elif time.time() >= deadline:
            _state["message"] = "登录会话超时，已关闭"
    except Exception as e:
        _state["message"] = f"登录会话异常: {e}"
        try:
            import logging
            logging.getLogger("avdb.javdb_login").error("登录会话异常", exc_info=True)
        except Exception:
            pass
    finally:
        _cleanup()


def _sync_top250_profile() -> bool:
    """登录成功后把主 profile（cookie）同步到 top250 通道 profile。

    双通道：top250 通道用独立浏览器目录（browser_profile_top250），
    与订阅通道并行跑。登录态只存在主 profile，这里整目录拷贝过去。
    拷贝前要求 top250 通道空闲（无 Chromium 使用该目录）。
    """
    import shutil
    from services.scraper_lock import is_running, CHANNEL_TOP250
    top250_dir = PROFILE_DIR.parent / "browser_profile_top250"
    if not PROFILE_DIR.exists():
        return False
    if is_running(CHANNEL_TOP250):
        return False  # top250 在跑，等下次登录再同步
    try:
        if top250_dir.exists():
            shutil.rmtree(top250_dir, ignore_errors=True)
        shutil.copytree(PROFILE_DIR, top250_dir, dirs_exist_ok=True)
        return True
    except Exception:
        return False


def ensure_top250_profile() -> bool:
    """top250 通道启动前引导：profile 缺失时从主 profile 拷贝（一次性）。"""
    top250_dir = PROFILE_DIR.parent / "browser_profile_top250"
    if top250_dir.exists() and any(top250_dir.iterdir()):
        return True
    return _sync_top250_profile()


def _do_submit(page, cmd: dict) -> dict:
    """在会话线程内执行提交（Playwright 对象同线程安全）。"""
    try:
        # 字段为 placeholder 式设计（Username or Email / Password / Captcha code），
        # 语言随账号设置切换英文/中文——placeholder 双语匹配 + type 兜底
        user_field = page.locator(
            "input[placeholder*='Username' i], input[placeholder*='用户' i], "
            "input[placeholder*='邮箱' i], input[placeholder*='账号' i], "
            "input[name='username'], input[name='email'], input[name='name'], #username").first
        pass_field = page.locator("input[type='password']:visible").first
        if not user_field.count() or not pass_field.count():
            return {"ok": False, "message": "登录表单字段未找到，请重开会话"}
        user_field.fill(cmd.get("username") or "")
        pass_field.fill(cmd.get("password") or "")
        cap_txt = cmd.get("captcha") or ""
        if cap_txt:
            cap = page.locator(
                "input[placeholder*='Captcha' i], input[placeholder*='验证码'], "
                "input[name='captcha'], #captcha, input[name='code']").first
            if not cap.count():
                return {"ok": False, "message": "验证码输入框未找到"}
            cap.fill(cap_txt)
        # 7 天免登录（减少重新登录频率）
        try:
            keep = page.locator("input[type='checkbox']").first
            if keep.count() and not keep.is_checked():
                keep.check()
        except Exception:
            pass
        page.locator(
            "button:has-text('Sign in'), button:has-text('登入'), button:has-text('登录'), "
            "input[type='submit'], button[type='submit']").first.click()
        for _ in range(10):
            page.wait_for_timeout(2000)
            if "login" not in (page.url or ""):
                break
        logged = "login" not in (page.url or "")
        if logged:
            _state["logged_in"] = True
            return {"ok": True, "message": "登录成功！cookie 已保存，后续爬取自动复用"}
        _state["message"] = "登录未成功（账号/密码/验证码有误或被拦截），看新截图重试"
        return {"ok": False, "message": _state["message"]}
    except Exception as e:
        return {"ok": False, "message": f"提交异常: {e}"}


class SubmitBody(BaseModel):
    username: str
    password: str
    captcha: str = ""


@router.post("/start")
def start(_admin: CurrentAdmin):
    from services.scraper_lock import is_running
    if is_running():
        raise HTTPException(status_code=409, detail="爬取任务运行中（共用浏览器配置），请稍后再登录")
    with _lock:
        if _state["running"]:
            raise HTTPException(status_code=409, detail="登录会话已在进行中")
        _state.update(running=True, logged_in=None, message="启动中…", started_at=time.time())
        _ctx["stop"] = False
    threading.Thread(target=_session_thread, daemon=True).start()
    return {"ok": True}


@router.get("/screenshot")
def screenshot(_admin: CurrentAdmin):
    if not _state["running"]:
        raise HTTPException(status_code=404, detail="无活跃登录会话")
    shot = _ctx.get("shot")
    if not shot:
        return {"ok": False, "message": _state["message"] or "浏览器启动中，请稍候…"}
    return {"ok": True, "image": shot}


@router.post("/submit")
def submit(body: SubmitBody, _admin: CurrentAdmin):
    if not _state["running"]:
        raise HTTPException(status_code=404, detail="无活跃登录会话")
    # 投递给会话线程执行（Playwright 对象不能跨线程调用）
    _ctx["cmd"] = {"action": "submit", "username": body.username,
                   "password": body.password, "captcha": body.captcha}
    for _ in range(25):  # 最长等 25s（提交流程内部 ~20s）
        time.sleep(1)
        r = _ctx.get("submit_result")
        if r is not None:
            _ctx["submit_result"] = None
            if r.get("ok"):
                _state["message"] = r["message"]
            else:
                _state["message"] = r["message"]
            return r
    return {"ok": False, "message": "提交处理超时，请稍后查看状态重试"}


@router.get("/status")
def status(_admin: CurrentAdmin):
    return {"running": _state["running"], "logged_in": _state["logged_in"],
            "message": _state["message"],
            "elapsed": int(time.time() - _state["started_at"]) if _state["running"] else 0}


@router.post("/cancel")
def cancel(_admin: CurrentAdmin):
    _ctx["cmd"] = {"action": "cancel"}
    _ctx["stop"] = True
    threading.Thread(target=lambda: (time.sleep(3), _cleanup()), daemon=True).start()
    _state["message"] = "已取消"
    return {"ok": True}
