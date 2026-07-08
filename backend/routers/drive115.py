"""115 网盘路由 —— OAuth 扫码 + 离线任务管理。"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from deps import CurrentUser
from services.drive115_client import (
    add_offline_task,
    exchange_token,
    get_quota,
    get_task_list,
    init_device_auth,
    poll_auth_status,
)

router = APIRouter(prefix="/api/drive115", tags=["drive115"])


class PushMagnetRequest(BaseModel):
    magnet: str


@router.post("/auth/init")
async def auth_init(_user: CurrentUser):
    """发起设备授权（返回扫码信息）。"""
    return await init_device_auth()


@router.get("/auth/poll")
async def auth_poll(uid: str, sign: str, _user: CurrentUser):
    """轮询扫码状态。"""
    return await poll_auth_status(uid, sign)


@router.post("/auth/exchange")
async def auth_exchange(uid: str, _user: CurrentUser):
    """扫码确认后换取 token。"""
    return await exchange_token(uid)


@router.post("/offline/add")
async def offline_add(req: PushMagnetRequest, _user: CurrentUser):
    """推送磁力到 115 离线下载。"""
    return await add_offline_task(req.magnet)


@router.get("/offline/tasks")
async def offline_tasks(_user: CurrentUser):
    """查询离线任务列表。"""
    return await get_task_list()


@router.get("/quota")
async def quota(_user: CurrentUser):
    """查询离线配额。"""
    return await get_quota()
