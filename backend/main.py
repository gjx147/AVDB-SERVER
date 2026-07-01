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
    # TODO Phase 3+: 启动 APScheduler / watchdog
    yield
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


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME}


@app.post("/api/auth/login")
def login():
    """占位登录端点 —— Phase 1 暂不启用强鉴权（AUTH_DISABLED）。

    后续接入用户表 + OAuth2PasswordRequestForm。
    """
    from auth import create_access_token
    token = create_access_token("admin")
    return {"access_token": token, "token_type": "bearer"}
