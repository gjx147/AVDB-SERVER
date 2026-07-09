"""Alembic 迁移环境配置。

- 自动发现 backend/models 下的所有 ORM 模型（autogenerate 支持）
- 从 backend/database.py 读取 DATABASE_URL，不硬编码在 alembic.ini
- 导入 models 包以触发所有表注册到 Base.metadata
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# 把 backend 加入 sys.path，使 import database / import models 可用
BACKEND_DIR = str(Path(__file__).resolve().parent.parent / "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# 导入 Base 与全部模型（触发 metadata 注册）
from database import Base, DATABASE_URL, engine as app_engine  # noqa: E402
import models  # noqa: E402,F401  # 注册所有表

# Alembic 配置对象
config = context.config

# 用 DATABASE_URL 覆盖 alembic.ini 里的占位 sqlalchemy.url
config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：仅用 URL 生成 SQL，不需要 DBAPI。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite ALTER 兼容
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：复用 backend/database.py 的 engine（已注册 PRAGMA）。

    Phase C 修复：原 engine_from_config 未注册 PRAGMA 监听器，
    导致迁移期间 foreign_keys=ON / WAL / busy_timeout 不生效。
    """
    # 复用已配置 PRAGMA 的 app engine
    connectable = app_engine

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite ALTER 兼容（重建表模式）
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
