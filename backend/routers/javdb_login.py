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
_ctx = {"pw": None, "ctx": None, "page": None, "stop": False}
TIMEOUT_S = 300
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"


def is_active() -> bool:
    return _state["running"]


def _db_path() -> Path:
    """与 backend DATABASE_URL 同源的 SQLite 路径。"""
    import os
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):])
    return Path("data/javdb.db")


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


def _proxy_server() -> str:
    v = _read_setting("http_proxy")
    if v and ("127.0.0.1" in v or "localhost" in v):
        v = v.replace("127.0.0.1", "host.docker.internal").replace("localhost", "host.docker.internal")
    return v


def _session_thread() -> None:
    from playwright.sync_api import sync_playwright
    try:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        pw = sync_playwright().start()
        _ctx["pw"] = pw
        kwargs = dict(user_agent=UA, headless=True,
                      channel="chromium",  # Docker 镜像只装完整版 chromium（与爬虫 scraper.py 同参），headless_shell 变体不存在
                      args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        proxy = _proxy_server()
        if proxy:
            kwargs["proxy"] = {"server": proxy}
        ctx = pw.chromium.launch_persistent_context(str(PROFILE_DIR), **kwargs)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        _ctx["ctx"], _ctx["page"] = ctx, page
        page.goto(f"{_read_setting('javdb_url', 'https://javdb.com').rstrip('/')}/login",
                  wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
        _state["message"] = "登录页已打开，看截图填账号、密码、验证码后提交"
        deadline = time.time() + TIMEOUT_S
        while time.time() < deadline and not _ctx["stop"] and _state["logged_in"] is not True:
            time.sleep(2)
        if _state["logged_in"] is True:
            time.sleep(2)  # cookie 落盘
            _state["message"] = "登录成功，cookie 已保存"
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
    page = _ctx.get("page")
    if not _state["running"]:
        raise HTTPException(status_code=404, detail="无活跃登录会话")
    if not page:
        # 浏览器仍在启动（Playwright 启动+导航需数秒），前端稍后重试
        return {"ok": False, "message": _state["message"] or "浏览器启动中，请稍候…"}
    try:
        png = page.screenshot(type="png")
        return {"ok": True, "image": "data:image/png;base64," + base64.b64encode(png).decode()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"截图失败: {e}")


@router.post("/submit")
def submit(body: SubmitBody, _admin: CurrentAdmin):
    page = _ctx.get("page")
    if not page or not _state["running"]:
        raise HTTPException(status_code=404, detail="无活跃登录会话")
    try:
        user_field = page.locator("input[name='username'], input[name='name'], #username, #name").first
        pass_field = page.locator("input[name='password'], #password").first
        if not user_field.count() or not pass_field.count():
            return {"ok": False, "message": "登录表单字段未找到，请重开会话"}
        user_field.fill(body.username)
        pass_field.fill(body.password)
        if body.captcha:
            cap = page.locator("input[name='captcha'], #captcha, input[name='code'], input[placeholder*='验证']").first
            if not cap.count():
                return {"ok": False, "message": "验证码输入框未找到"}
            cap.fill(body.captcha)
        page.locator("button[type='submit'], input[type='submit'], button:has-text('登')").first.click()
        for _ in range(10):
            page.wait_for_timeout(2000)
            if "login" not in (page.url or ""):
                break
        logged = "login" not in (page.url or "")
        if logged:
            _state["logged_in"] = True
            _state["message"] = "登录成功！cookie 已保存，后续爬取自动复用"
            return {"ok": True, "message": _state["message"]}
        _state["message"] = "登录未成功（账号/密码/验证码有误或被拦截），看新截图重试"
        return {"ok": False, "message": _state["message"]}
    except Exception as e:
        return {"ok": False, "message": f"提交异常: {e}"}


@router.get("/status")
def status(_admin: CurrentAdmin):
    return {"running": _state["running"], "logged_in": _state["logged_in"],
            "message": _state["message"],
            "elapsed": int(time.time() - _state["started_at"]) if _state["running"] else 0}


@router.post("/cancel")
def cancel(_admin: CurrentAdmin):
    _ctx["stop"] = True
    _cleanup()
    _state["message"] = "已取消"
    return {"ok": True}
