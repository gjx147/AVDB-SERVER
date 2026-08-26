# syntax=docker/dockerfile:1
# AVDB-SERVER Dockerfile
# 多阶段构建：Stage1 Node 构建前端 dist → Stage2 Python slim + 依赖 + Playwright Chromium + 应用代码

ARG PYTHON_VERSION=3.12

# ── Stage 1: 前端构建（node:20-alpine）──
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
# 先复制 package.json 与锁文件，命中缓存时跳过 npm ci
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
# 产物：/app/frontend/dist

# ── Stage 2: Python 运行时 ──
FROM python:${PYTHON_VERSION}-slim

LABEL maintainer="AVDB-SERVER"
LABEL description="JavDB 影片元数据采集与管理系统"

# 构建参数（中国大陆镜像加速，可由 build-arg 关闭）
ARG USE_CN_MIRROR=true
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG DEBIAN_MIRROR=mirrors.tuna.tsinghua.edu.cn

# 配置 Debian 镜像源（加速 apt）
RUN if [ "$USE_CN_MIRROR" = "true" ] && [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|deb.debian.org|${DEBIAN_MIRROR}|g; s|security.debian.org|${DEBIAN_MIRROR}|g" \
            /etc/apt/sources.list.d/debian.sources; \
    fi

# 安装 Playwright Chromium 运行时依赖 + curl(healthcheck) + 中日韩字体
# 换国内 apt 源（deb.debian.org 直连超时，构建环境适配；不影响运行时）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list || true
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
        libcups2 libdrm2 libdbus-1-3 libxkbcommon0 libxcomposite1 \
        libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
        libcairo2 libasound2 libatspi2.0-0 \
        fonts-liberation fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 装全部 Python 依赖
ENV PIP_INDEX_URL=${PIP_INDEX_URL}
ENV PIP_NO_CACHE_DIR=1
COPY backend/requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# 安装 Playwright Chromium 浏览器（国内镜像加速）
# 只装完整 chromium（npmmirror 不提供 chromium-headless-shell，browser_pool 用 channel="chromium" 启动完整 chromium）
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ARG PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
# chromium 必须成功（失败即终止构建）；ffmpeg（视频转码用，本系统不依赖）
# 若镜像源缺失则显式警告继续，避免"假成功"与"假失败"两种极端
RUN python -m playwright install chromium || \
    (ls /ms-playwright/chromium-* >/dev/null 2>&1 && echo "WARN: playwright ffmpeg 下载失败（不影响爬虫/截图主功能），继续构建" || exit 1)

# 复制应用代码
COPY backend/ ./backend/
COPY magnet_scraper/ ./magnet_scraper/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# 环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app:/app/backend
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV BACKEND_URL=http://127.0.0.1:8000
ENV DATA_DIR=/app/data
ENV IMAGES_DIR=/app/data/images

# 数据持久化卷
VOLUME ["/app/data"]

EXPOSE 8000

# 创建非 root 用户
# 只 chown /app（小层）。/ms-playwright（Chromium，~500MB）改由 entrypoint 运行时
# chown——否则这层会随每次 COPY 代码变化而重建，推送时要重传 500MB。
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /bin/sh appuser \
    && chown -R appuser:appuser /app

# entrypoint: root 启动修复 data 权限 → 降权 appuser 执行
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD cd /app && alembic upgrade heads && cd backend && exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
