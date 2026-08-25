"""应用配置 —— 从环境变量读取（pydantic-settings）。

敏感字段（密码、JWT 密钥、下载器凭据）走环境变量；
业务配置（javdb_url 等）走数据库 settings 表（运行时可改）。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
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
    # 留空时首次启动自动生成随机密钥并持久化到 data/.secret_key
    SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 天

    # 管理员账号（首次启动写入 DB，之后可改密码）
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin"  # 首次启动用，之后应改

    # 若设为 true 则跳过鉴权（本地开发/内网）
    AUTH_DISABLED: bool = False

    @model_validator(mode="after")
    def _validate_auth_disabled(self):
        """T7: AUTH_DISABLED 仅允许在 DEBUG=true 时开启（防止误配置导致全站裸奔）。"""
        if self.AUTH_DISABLED and not self.DEBUG:
            raise ValueError("AUTH_DISABLED 仅允许在 DEBUG=true 时开启（安全保护）")
        return self

    # --- CD2（云盘客户端）---
    # 是否校验 CD2 gRPC-Web 的 TLS 证书；默认 False 保持现状（兼容自签证书环境）。
    # 生产/公网环境应设为 True，防止 CD2 登录口令在传输中被中间人窃听。
    CD2_SSL_VERIFY: bool = False

    # --- 115 云盘 ---
    # 115 开放平台 client_id（需实际申请；占位值仅本地调试）
    DRIVE115_CLIENT_ID: str = "AVDB-SERVER"

    # --- 爬虫 ---
    # 是否在启动时预热 Playwright 浏览器（NAS 低内存环境可关闭，按需启动）
    BROWSER_PREWARM: bool = True
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
