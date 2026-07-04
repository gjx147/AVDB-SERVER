"""元数据聚合路由 —— 触发多源抓取补充任务元数据。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from deps import CurrentUser
from services.data_aggregator import enrich_task

router = APIRouter(prefix="/api/aggregate", tags=["aggregate"])


@router.post("/{task_id}")
async def enrich(task_id: int, overwrite: bool = False, _user: CurrentUser = None):
    """对单个任务执行多源元数据聚合。overwrite=True 全覆盖已有字段。"""
    result = await enrich_task(task_id, overwrite=overwrite)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "聚合失败"))
    return result
