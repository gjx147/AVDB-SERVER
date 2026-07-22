"""AVDB-SERVER FastAPI 入口。

设计要点（区别于 AVDB 补丁层）：
- 用 lifespan 启动后台服务，不在 import-time 跑迁移/启动线程（避免启动阻塞）
- 鉴权走 OAuth2 + JWT（见 auth.py），不靠补丁层 ASGI 包装
- routers 在后续 Phase 逐步挂载
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi import status as http_status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pathlib import Path
from starlette.staticfiles import StaticFiles
from sqlalchemy.orm import Session

# 让 backend 目录自身可被 import
sys.path.insert(0, os.path.dirname(__file__))

from config import get_settings
from database import get_db
from deps import get_current_admin

logger = logging.getLogger("avdb.main")
logging.basicConfig(
    level=logging.DEBUG if get_settings().DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# 下载器专用日志文件（data/downloaders.log），记录所有推送/测试操作
_dl_log_path = Path(get_settings().DATA_DIR) / "downloaders.log"
_dl_log_path.parent.mkdir(parents=True, exist_ok=True)
_dl_file_handler = logging.FileHandler(_dl_log_path, encoding="utf-8")
_dl_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
for _dl_logger_name in ("avdb.downloaders", "avdb.downloaders.cd2", "avdb.downloaders.cms"):
    _dl_l = logging.getLogger(_dl_logger_name)
    _dl_l.addHandler(_dl_file_handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时建库/迁移、启动后台调度；关闭时清理。

    注意：迁移用 Alembic 在部署阶段执行，这里只做兜底建表（开发环境）。
    后台服务（scheduler/watchdog）在后续 Phase 接入。
    """
    logger.info("AVDB-SERVER 启动中…")
    # 确保管理员账号存在（首次启动创建，SECRET_KEY 默认值告警）
    from auth import ensure_admin_exists
    ensure_admin_exists()
    # 开发环境兜底：确保表存在（生产用 alembic upgrade head）
    from database import Base, engine
    if app_settings.DEBUG:
        Base.metadata.create_all(bind=engine)
        logger.info("开发模式：create_all 兜底建表")
    else:
        logger.info("生产模式：依赖 alembic upgrade head（已在 CMD 中执行）")
    # 启动浏览器池（按需启动，首次 acquire 时才真正 launch）
    from services.browser_pool import browser_pool
    try:
        await browser_pool.start()
    except Exception as e:
        logger.warning(f"浏览器池启动失败（按需重试）: {e}")
    # 启动调度中心
    from services.scheduler import start_scheduler, stop_scheduler
    try:
        await start_scheduler()
        import os
        # 注册 auto_crawl（需 AUTO_CRAWL_ENABLED=true）
        if os.environ.get("AUTO_CRAWL_ENABLED", "false").lower() == "true":
            from services.auto_crawl import register_jobs
            register_jobs()
        # 注册订阅巡检（需 SUBSCRIPTION_MONITOR_ENABLED=true，默认开）
        if os.environ.get("SUBSCRIPTION_MONITOR_ENABLED", "true").lower() == "true":
            from services.subscription_monitor import register_job
            register_job(interval_hours=int(os.environ.get("SUBSCRIPTION_CHECK_INTERVAL_H", "6")))
        # 注册下载进度追踪（需 DOWNLOAD_TRACKER_ENABLED=true，默认开）
        if os.environ.get("DOWNLOAD_TRACKER_ENABLED", "true").lower() == "true":
            from services.download_tracker import register_job as register_tracker
            register_tracker(interval=int(os.environ.get("DOWNLOAD_TRACK_INTERVAL_S", "60")))
        # 注册数据库定时备份（默认每天凌晨 3 点）
        from services.backup import register_job as register_backup
        register_backup()
        # 注册自动重试（从 DB settings 读 auto_retry_enabled）
        from services.auto_retry import register_job as register_retry
        register_retry(interval=int(os.environ.get("AUTO_RETRY_INTERVAL_S", "300")))
        # 注册排行榜自动爬取（从 DB settings 读 ranking_auto_crawl）
        from services.ranking_auto_crawl import register_job as register_ranking
        register_ranking(interval_hours=int(os.environ.get("RANKING_AUTO_INTERVAL_H", "24")))
        logger.info("调度任务注册完成")
    except Exception as e:
        logger.warning(f"调度中心启动失败: {e}")
    yield
    # 关闭调度中心
    try:
        await stop_scheduler()
    except Exception:
        logger.warning("调度中心关闭异常", exc_info=True)
    try:
        await browser_pool.stop()
    except Exception:
        logger.warning("浏览器池关闭异常", exc_info=True)


settings = get_settings()
app = FastAPI(
    title=settings.APP_NAME,
    description="JavDB 影片元数据采集与管理系统",
    version="0.1.0",
    lifespan=lifespan,
)
# 保留引用避免被下方 routers.settings 覆盖
app_settings = settings

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载路由
from routers import list_sources, tasks, crawl, status, actors, aggregate, rankings, subscriptions, insights, ai, content_filter, media_server, images, favorites, downloaders, downloads, settings, dashboard, v2, drive115, magnet_search, system  # noqa: E402
app.include_router(list_sources.router)
app.include_router(tasks.router)
app.include_router(crawl.router)
app.include_router(status.router)
app.include_router(actors.router)
app.include_router(aggregate.router)
app.include_router(rankings.router)
app.include_router(subscriptions.router)
app.include_router(insights.router)
app.include_router(ai.router)
app.include_router(content_filter.router)
app.include_router(media_server.router)
app.include_router(images.router)
app.include_router(favorites.router)
app.include_router(downloaders.router)
app.include_router(downloads.router)
app.include_router(settings.router)
app.include_router(dashboard.router)
app.include_router(v2.router)
app.include_router(drive115.router)
app.include_router(magnet_search.router)
app.include_router(system.router)


@app.get("/api/health")
def health():
    """存活探针（liveness）—— 不检查依赖，仅确认进程存活。"""
    return {"status": "ok", "app": app_settings.APP_NAME}


@app.get("/api/health/ready")
def health_ready(db: Session = Depends(get_db)):
    """就绪探针（readiness）—— 检查 DB 连接，迁移完成前返回 503。

    用于反向代理/Docker healthcheck 判断服务是否可接受流量。
    """
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        return {"status": "ready", "db": "ok"}
    except Exception as e:
        from starlette.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "error": str(e)},
        )


@app.get("/api/scheduler/jobs")
def scheduler_jobs(_admin: str = Depends(get_current_admin)):
    """列出调度中心所有任务（需要管理员权限）。"""
    from services.scheduler import list_jobs
    return {"jobs": list_jobs()}


@app.post("/api/notify/test")
async def notify_test(_admin: str = Depends(get_current_admin)):
    """测试通知（需要管理员权限）。"""
    from services.notifier import test_notify
    return {"results": await test_notify()}


@app.post("/api/auth/login")
def login(
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """用户登录 —— 校验凭据，签发 JWT。

    接收 OAuth2PasswordRequestForm 格式的 username/password，
    查 User 表验证，返回 access_token。
    """
    from auth import authenticate_user, create_access_token

    user = authenticate_user(db, username, password)
    if user is None:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(user.username)
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user.username,
        "is_admin": user.is_admin,
    }


# --- 前端 SPA 静态文件服务 ---
# 静态文件最后挂载（否则会覆盖 API 路由）。
# frontend/dist 在容器内为 /app/frontend/dist，本地开发为项目根 frontend/dist。
from pathlib import Path  # noqa: E402
from fastapi.responses import FileResponse, Response  # noqa: E402
from starlette.staticfiles import StaticFiles  # noqa: E402

# ── WebSocket：爬取实时进度推送 ──
# 前端 Crawl 页通过 /ws/crawl-progress 订阅实时进度。
# 后端每 2 秒读取 crawl status（进程级 + crawl_status.json）并推送，
# 复用 routers.crawl.crawl_status 的逻辑，避免重复实现。
from fastapi import WebSocket, WebSocketDisconnect  # noqa: E402


@app.websocket("/ws/crawl-progress")
async def crawl_progress_ws(websocket: WebSocket):
    """爬取进度 WebSocket：每 2 秒推送一次 crawl status。"""
    await websocket.accept()
    try:
        import asyncio
        from routers.crawl import crawl_status as _get_crawl_status
        from deps import CurrentUser  # noqa: F401  (类型引用)

        while True:
            try:
                # crawl_status 需要 _user: CurrentUser 依赖注入，
                # 但 WebSocket 上下文中不走 Depends，直接调用函数（传 None）
                # 函数内部不实际使用 _user，只是鉴权标记。
                status = _get_crawl_status(None)  # type: ignore
                await websocket.send_json(status)
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.debug("crawl_progress_ws 推送异常: %s", e)
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("crawl_progress_ws 连接关闭: %s", e)

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


class SPAStaticFiles(StaticFiles):
    """SPA 静态文件：API 路径返回 404，WebSocket 直通，其余 fallback 到 index.html。"""

    async def __call__(self, scope, receive, send):
        # WebSocket 连接不走静态文件（scope 里没有 method 字段）
        if scope.get("type") == "websocket":
            await Response("Not Found", status_code=404)(scope, receive, send)
            return
        request_path = scope.get("path", "")
        if request_path.startswith("/api/") or request_path.startswith("/ws"):
            await Response("Not Found", status_code=404)(scope, receive, send)
            return
        path = request_path.lstrip("/")
        file_path = Path(self.directory) / path
        if path and file_path.exists() and file_path.is_file():
            await super().__call__(scope, receive, send)
            return
        index_file = Path(self.directory) / "index.html"
        if index_file.exists():
            await FileResponse(str(index_file))(scope, receive, send)
        else:
            await Response("Not Found", status_code=404)(scope, receive, send)


if _FRONTEND_DIST.exists():
    app.mount("/", SPAStaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")
else:
    logger.warning(f"前端构建目录不存在: {_FRONTEND_DIST}（请先 npm run build）")


# ── 全局异常处理器（Phase 3：统一 500 格式，不泄露 traceback）──

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    from starlette.responses import JSONResponse
    logger.exception("未处理的异常: %s", str(exc))
    return JSONResponse(
        status_code=500,
        content={"ok": False, "message": "服务器内部错误"},
    )

