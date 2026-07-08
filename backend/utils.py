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


def get_or_404(db: Session, model, obj_id: int, name: str = "资源"):
    """通用 get + 404 模式（原来在 20+ 个 router 端点中重复）。

    Usage:
        task = get_or_404(db, Task, task_id, "任务")
        actor = get_or_404(db, Actor, actor_id, "演员")
    """
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{name}不存在")
    return obj


def paginate(db: Session, stmt, pagination: tuple[int, int]):
    """通用分页查询（原来在 6 个 router 中重复）。

    Returns (total, items).
    """
    from sqlalchemy import func, select
    offset, limit = pagination
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = db.execute(stmt.offset(offset).limit(limit)).scalars().all()
    return total, items
