"""系统路由 —— 磁盘信息等。兼容 AVDB 前端 GET /api/system/disk。"""

from __future__ import annotations

import shutil
from fastapi import APIRouter
from config import get_settings
from deps import CurrentUser

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/disk")
def disk_info(_user: CurrentUser):
    """磁盘使用情况。返回前端 DiskInfo 期望的嵌套结构。"""
    data_dir = get_settings().DATA_DIR
    try:
        usage = shutil.disk_usage(data_dir)
        total_gb = round(usage.total / 1024**3, 1)
        used_gb = round(usage.used / 1024**3, 1)
        free_gb = round(usage.free / 1024**3, 1)
        free_percent = round((1 - usage.used / usage.total) * 100, 1)
    except Exception:
        total_gb = used_gb = free_gb = free_percent = 0

    import os
    images_dir = os.path.join(data_dir, "images")
    images_size = 0
    images_count = 0
    if os.path.isdir(images_dir):
        for root, _, files in os.walk(images_dir):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    images_size += os.path.getsize(fp)
                    images_count += 1
                except Exception:
                    pass

    db_path = os.path.join(data_dir, "javdb.db")
    db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0

    return {
        "data": {
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "free_percent": free_percent,
        },
        "images_size_mb": round(images_size / 1024 / 1024, 1),
        "images_count": images_count,
        "db_size_mb": round(db_size / 1024 / 1024, 1),
    }
