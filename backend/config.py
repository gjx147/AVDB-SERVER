"""应用配置 —— 从环境变量读取（pydantic-settings）。

敏感字段（密码、JWT 密钥、下载器凭据）走环境变量；
业务配置（javdb_url 等）走数据库 settings 表（运行时可改）。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- 应用 ---
    APP_NAME: str = "AVDB-SERVER"
    DEBUG: bool = False

    # --- 数据库（空则用默认 SQLite data/javdb.db）---
    DATABASE_URL: str = ""

    # --- 鉴权 ---
    # JWT 密钥；生产环境务必通过环境变量覆盖。
    SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 天

    # 管理员账号（首次启动写入 DB，之后可改密码）
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin"  # 首次启动用，之后应改

    # 若设为 true 则跳过鉴权（本地开发/内网）
    AUTH_DISABLED: bool = False

    # --- 爬虫 ---
    JAVDB_URL: str = "https://javdb.com"
    SCRAPER_PYTHON: str = ""  # 空=用当前 python

    # --- 代理（Playwright/httpx 用）---
    HTTP_PROXY: str = ""
    HTTPS_PROXY: str = ""

    # --- 目录 ---
    DATA_DIR: str = "data"
    IMAGES_DIR: str = "data/images"


@lru_cache
def get_settings() -> Settings:
    return Settings()
