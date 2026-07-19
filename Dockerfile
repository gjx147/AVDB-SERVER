# syntax=docker/dockerfile:1
# AVDB-SERVER Dockerfile
# 单阶段构建：Python slim + 依赖 + Playwright Chromium + 应用代码

ARG PYTHON_VERSION=3.12

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
# 忽略 headless-shell 下载失败（npmmirror 无此文件，但 chromium 主包足够）
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ARG PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
RUN PLAYWRIGHT_DOWNLOAD_HOST=${PLAYWRIGHT_DOWNLOAD_HOST} python -m playwright install chromium; exit 0

# 复制应用代码
COPY backend/ ./backend/
COPY magnet_scraper/ ./magnet_scraper/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY frontend/ ./frontend/

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
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /bin/sh appuser \
    && chown -R appuser:appuser /app /ms-playwright

# entrypoint: root 启动修复 data 权限 → 降权 appuser 执行
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD cd /app && alembic upgrade head && cd backend && exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
