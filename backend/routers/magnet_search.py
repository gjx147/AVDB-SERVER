"""磁力多源搜索路由。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from deps import CurrentUser
from services.magnet_sources import search_all

router = APIRouter(prefix="/api/magnet-search", tags=["magnet-search"])


@router.get("/{code}")
async def search(
    code: str,
    _user: CurrentUser,
    sources: str | None = Query(None, description="逗号分隔的源名，空则全部"),
):
    """多源磁力搜索。"""
    source_list = sources.split(",") if sources else None
    return await search_all(code, source_list)
