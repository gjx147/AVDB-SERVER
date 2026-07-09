"""SQLite 热备份服务 —— 定时 .backup 到 data/backups/ 目录。

WAL 模式下不能直接 cp javdb.db（会得到不一致副本），
必须用 SQLite 的 backup API（在线热备份）。
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from config import get_settings

logger = logging.getLogger("avdb.backup")


async def run_backup(retention_days: int = 7) -> dict:
    """执行一次 SQLite 热备份。

    备份到 {DATA_DIR}/backups/javdb-YYYYMMDD-HHMMSS.db
    保留最近 retention_days 天的备份，更早的自动删除。
    """
    settings = get_settings()
    data_dir = Path(settings.DATA_DIR)
    db_path = data_dir / "javdb.db"
    backup_dir = data_dir / "backups"

    if not db_path.exists():
        return {"ok": False, "message": "数据库不存在"}

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"javdb-{timestamp}.db"

    try:
        # SQLite 在线热备份 API
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(str(backup_path))
        src.backup(dst)
        dst.close()
        src.close()
        logger.info("数据库备份完成: %s (%d KB)", backup_path.name, backup_path.stat().st_size // 1024)
    except Exception as e:
        logger.error("数据库备份失败: %s", e)
        return {"ok": False, "message": str(e)}

    # 清理旧备份
    deleted = 0
    cutoff = datetime.utcnow().timestamp() - retention_days * 86400
    for f in backup_dir.glob("javdb-*.db"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            deleted += 1
    if deleted:
        logger.info("清理 %d 个过期备份(>%d天)", deleted, retention_days)

    return {"ok": True, "backup": backup_path.name, "cleaned_old": deleted}


def register_job(hour: int = 3, minute: int = 0) -> None:
    """注册定时备份到调度中心（默认每天凌晨 3 点）。"""
    from services.scheduler import add_cron_job

    add_cron_job(run_backup, "db-backup", hour=hour, minute=minute)
    logger.info("数据库备份已注册: 每天 %02d:%02d", hour, minute)
