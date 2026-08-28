"""无番号任务清理：三类任务自动删除。

1. 番号提取失败（video_code=NULL，error_message 有记录）
2. 无标准番号的条目（合集/素人/VR 特殊条目）
3. 页面重定向到不匹配内容

统一判定：tasks.video_code IS NULL（无法按番号查询/推送/入库，无保留价值）。
删除走 SQLAlchemy 级联（与任务删除工具同机制：关联的 actor_movies/
task_collections/magnets 一并清理）。
"""
from __future__ import annotations

import logging

from sqlalchemy import delete, select, func

logger = logging.getLogger("avdb.cleanup")


def count_no_code_tasks(db) -> int:
    """统计无番号任务数（预检用）。"""
    from models import Task
    return db.execute(select(func.count()).where(Task.video_code.is_(None))).scalar() or 0


def cleanup_no_code_tasks(db, dry_run: bool = True) -> dict:
    """删除无番号任务。dry_run=True 仅统计（默认安全模式）。

    返回 {no_code, deleted}。
    """
    from models import Task
    no_code = count_no_code_tasks(db)
    if dry_run or no_code == 0:
        return {"no_code": no_code, "deleted": 0, "dry_run": dry_run}

    # 先收集 id（避免删除过程中游标漂移）
    ids = db.execute(select(Task.id).where(Task.video_code.is_(None))).scalars().all()
    if not ids:
        return {"no_code": no_code, "deleted": 0, "dry_run": dry_run}

    # 清理关联表（显式删，防外键中断；磁力存 magnets_json 随行删除）
    from models import actor_movies, task_collections
    for table in (actor_movies, task_collections):
        try:
            db.execute(table.delete().where(table.c.task_id.in_(ids)))
        except Exception:
            pass

    deleted = db.execute(delete(Task).where(Task.id.in_(ids))).rowcount
    db.commit()
    logger.info("无番号任务清理: 删除 %d 个（无番号共 %d）", deleted, no_code)
    return {"no_code": no_code, "deleted": deleted, "dry_run": False}
