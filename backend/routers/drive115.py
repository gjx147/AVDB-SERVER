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
    """115 离线配额/空间用量（F12）：规范化返回 total/used/remain（字节）。"""
    r = await get_quota()
    if "error" in r:
        return {"ok": False, "message": r["error"]}
    if r.get("ok") is False:
        return {"ok": False, "message": r.get("message") or "115 未授权"}
    # 115 官方失败时 HTTP 200 但 state != 1
    if isinstance(r.get("state"), int) and r.get("state") != 1:
        return {"ok": False, "message": f"115 接口错误: {r.get('message') or r.get('error') or r}"}
    data = r.get("data") or r
    # 口径兼容：data.quota / data.size / data（get_quota_info 变体）
    quota_obj = data.get("quota") or data.get("size") or data
    if isinstance(quota_obj, dict):
        quota_obj = {k: v for k, v in quota_obj.items()}

    def _num(v):
        try:
            return int(v)
        except Exception:
            return None

    return {
        "ok": True,
        "total": _num(quota_obj.get("total")),
        "used": _num(quota_obj.get("used")),
        "remain": _num(quota_obj.get("remain")),
    }
