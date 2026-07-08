"""系统路由 —— 磁盘信息等。兼容 AVDB 前端 GET /api/system/disk。"""

from __future__ import annotations

import shutil
from fastapi import APIRouter
from config import get_settings
from deps import CurrentUser

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/disk")
def disk_info(_user: CurrentUser):
    """磁盘使用情况（data 目录所在盘）。"""
    data_dir = get_settings().DATA_DIR
    try:
        usage = shutil.disk_usage(data_dir)
        return {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": round(usage.used / usage.total * 100, 1),
        }
    except Exception:
        return {"total": 0, "used": 0, "free": 0, "percent": 0}
