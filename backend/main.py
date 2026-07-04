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

# 让 backend 目录自身可被 import（uvicorn 在 backend/ 下启动时 __package__ 为空）
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings

logger = logging.getLogger("avdb")
logging.basicConfig(
    level=logging.DEBUG if get_settings().DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时建库/迁移、启动后台调度；关闭时清理。

    注意：迁移用 Alembic 在部署阶段执行，这里只做兜底建表（开发环境）。
    后台服务（scheduler/watchdog）在后续 Phase 接入。
    """
    logger.info("AVDB-SERVER 启动中…")
    # 开发环境兜底：确保表存在（生产用 alembic upgrade head）
    from database import Base, engine
    Base.metadata.create_all(bind=engine)
    logger.info("数据库就绪")
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
        # 注册 auto_crawl 默认任务（可由环境变量 AUTO_CRAWL_ENABLED 关闭）
        import os
        if os.environ.get("AUTO_CRAWL_ENABLED", "false").lower() == "true":
            from services.auto_crawl import register_jobs
            register_jobs()
    except Exception as e:
        logger.warning(f"调度中心启动失败: {e}")
    yield
    # 关闭调度中心
    try:
        await stop_scheduler()
    except Exception:
        pass
    # 关闭浏览器池
    try:
        await browser_pool.stop()
    except Exception:
        pass
    logger.info("AVDB-SERVER 关闭")


settings = get_settings()
app = FastAPI(
    title=settings.APP_NAME,
    description="JavDB 影片元数据采集与管理系统",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载路由
from routers import list_sources, tasks, crawl, status, actors, aggregate, rankings, subscriptions  # noqa: E402
app.include_router(list_sources.router)
app.include_router(tasks.router)
app.include_router(crawl.router)
app.include_router(status.router)
app.include_router(actors.router)
app.include_router(aggregate.router)
app.include_router(rankings.router)
app.include_router(subscriptions.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME}


@app.get("/api/scheduler/jobs")
def scheduler_jobs():
    """列出调度中心所有任务。"""
    from services.scheduler import list_jobs
    return {"jobs": list_jobs()}


@app.post("/api/auth/login")
def login():
    """占位登录端点 —— Phase 1 暂不启用强鉴权（AUTH_DISABLED）。

    后续接入用户表 + OAuth2PasswordRequestForm。
    """
    from auth import create_access_token
    token = create_access_token("admin")
    return {"access_token": token, "token_type": "bearer"}


# --- 前端 SPA 静态文件服务 ---
# 静态文件最后挂载（否则会覆盖 API 路由）。
# frontend/dist 在容器内为 /app/frontend/dist，本地开发为项目根 frontend/dist。
from pathlib import Path  # noqa: E402
from fastapi.responses import FileResponse, Response  # noqa: E402
from starlette.staticfiles import StaticFiles  # noqa: E402

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


class SPAStaticFiles(StaticFiles):
    """SPA 静态文件：API 路径返回 404，其余未匹配路径 fallback 到 index.html。"""

    async def __call__(self, scope, receive, send):
        request_path = scope["path"]
        if request_path.startswith("/api/"):
            # API 路径未匹配到路由，返回 404
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

