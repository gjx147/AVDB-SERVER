"""SQLite 热备份服务 —— 定时 .backup 到 data/backups/ 目录。

WAL 模式下不能直接 cp javdb.db（会得到不一致副本），
必须用 SQLite 的 backup API（在线热备份）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from config import get_settings

logger = logging.getLogger("avdb.backup")


def _sqlite_backup(src_path: str, dst_path: str) -> None:
    """SQLite 在线热备份（同步阻塞，由调用方 asyncio.to_thread 调度）。"""
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


async def run_backup(retention_days: int = 7) -> dict:
    """执行一次 SQLite 热备份。

    备份到 {DATA_DIR}/backups/javdb-YYYYMMDD-HHMMSS.db
    保留最近 retention_days 天的备份，更早的自动删除。
    """
    from database import DATABASE_URL

    settings = get_settings()
    # 仅支持 SQLite 在线热备份；PG 等其它后端没有本地 .db 文件，明确报错而不是"假成功"
    if not DATABASE_URL.startswith("sqlite"):
        scheme = DATABASE_URL.split("://", 1)[0] or DATABASE_URL
        logger.error("数据库备份失败: 当前数据库非 SQLite（%s），不支持在线热备份", scheme)
        return {"ok": False, "message": f"当前数据库非 SQLite（{scheme}），不支持备份"}

    data_dir = Path(settings.DATA_DIR)
    db_path = data_dir / "javdb.db"
    backup_dir = data_dir / "backups"

    if not db_path.exists():
        return {"ok": False, "message": "数据库不存在"}

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"javdb-{timestamp}.db"

    try:
        # SQLite 在线热备份 API（阻塞调用放线程池，避免卡事件循环）
        await asyncio.to_thread(_sqlite_backup, str(db_path), str(backup_path))
        # T9: 备份加密（Fernet，密钥由 SECRET_KEY 派生；开启后生成 .enc 并删除明文）
        if get_settings().BACKUP_ENCRYPT:
            from cryptography.fernet import Fernet
            import base64
            import hashlib

            secret = get_settings().SECRET_KEY
            if not secret:
                logger.warning("BACKUP_ENCRYPT=true 但 SECRET_KEY 为空，跳过加密（备份保持明文）")
            else:
                key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
                enc_path = backup_dir / f"javdb-{timestamp}.db.enc"
                with open(backup_path, "rb") as src, open(enc_path, "wb") as dst:
                    dst.write(b"AVDBENC1")
                    dst.write(Fernet(key).encrypt(src.read()))
                backup_path.unlink()
                backup_path = enc_path
                logger.info("数据库备份已加密: %s", enc_path.name)
        logger.info("数据库备份完成: %s (%d KB)", backup_path.name, backup_path.stat().st_size // 1024)
    except Exception as e:
        logger.error("数据库备份失败: %s", e)
        return {"ok": False, "message": str(e)}

    # 清理旧备份（明文 .db 与加密 .enc 都清理）
    deleted = 0
    cutoff = datetime.utcnow().timestamp() - retention_days * 86400
    for f in list(backup_dir.glob("javdb-*.db")) + list(backup_dir.glob("javdb-*.db.enc")):
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
