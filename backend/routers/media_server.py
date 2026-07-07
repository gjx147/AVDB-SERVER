"""媒体服务器路由 —— 在库状态查询/同步。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from deps import CurrentUser
from services.media_server import check_in_library, sync_library_status

router = APIRouter(prefix="/api/media-server", tags=["media-server"])


@router.get("/check/{video_code}")
async def check_code(video_code: str, _user: CurrentUser):
    """查询单个番号是否在库。"""
    return {"video_code": video_code, "in_library": await check_in_library(video_code)}


@router.post("/sync")
async def sync(_user: CurrentUser, limit: int = Query(200, le=1000)):
    """批量同步在库状态。"""
    return await sync_library_status(limit)
