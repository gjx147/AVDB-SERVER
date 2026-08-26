"""公共工具函数 —— 消除 20+ 处代码重复。

Phase 3 提取：
- get_setting / set_setting: 原来在 3 个文件中各定义一次
- get_or_404: 原来在所有 router 中各写一遍 obj = db.get(); if not obj: raise 404
- paginate: 原来在 6 个 router 中各写一遍分页逻辑
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Setting


def get_setting(db: Session, key: str, default: str = "") -> str:
    """读 Setting 表的值（原来在 downloaders/download_tracker/drive115 各定义一次）。"""
    row = db.get(Setting, key)
    return row.value if row and row.value else default


def set_setting(db: Session, key: str, value: str) -> None:
    """写 Setting 表（原来在 drive115_client 中定义 2 次）。"""
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.commit()


# 注：get_or_404 / paginate 曾在 Phase 3 提取，但各 router 仍手写实现，
# 两个函数当前全项目无引用（2026-08 审查确认），已移除；如需推广请先在调用点接入。


# 敏感配置键统一判定（settings 路由 / agent_service / 回滚端点共用，防止清单漂移）
_SENSITIVE_PATTERNS = ("password", "token", "secret", "key", "apikey", "api_key",
                       "cookie", "session", "passwd", "credential", "auth", "jwt")


def is_sensitive_key(key: str) -> bool:
    return any(p in key.lower() for p in _SENSITIVE_PATTERNS)
