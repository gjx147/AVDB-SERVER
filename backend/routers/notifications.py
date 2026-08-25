"""通知中心（F2）：历史查询 + 事件列表 + 各通道测试 + 免打扰时段。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from deps import CurrentAdmin, CurrentUser, DbSession
from models import NotifyLog, Setting

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
def list_notifications(
    db: DbSession, _user: CurrentUser,
    limit: int = Query(50, le=200),
    event: str = "",
):
    """通知历史（倒序）。"""
    q = select(NotifyLog).order_by(NotifyLog.id.desc()).limit(limit)
    if event:
        q = q.where(NotifyLog.event == event)
    rows = db.execute(q).scalars().all()
    return {
        "items": [
            {
                "id": r.id, "event": r.event, "title": r.title, "body": (r.body or "")[:200],
                "channel": r.channel, "ok": r.ok, "message": r.message,
                "created_at": str(r.created_at),
            }
            for r in rows
        ]
    }


@router.get("/events")
def list_events(_user: CurrentUser):
    """可订阅的通知事件清单。"""
    from services.notifier import ALL_EVENTS
    return {"events": sorted(ALL_EVENTS)}


@router.post("/test")
async def test_channels(_user: CurrentAdmin):
    """向所有已配置通道发送测试通知。"""
    from services.notifier import test_notify
    return await test_notify()


@router.get("/dnd")
def get_dnd(db: DbSession, _user: CurrentUser):
    """读取免打扰时段（HH:MM）。"""
    def _get(key: str) -> str:
        row = db.get(Setting, key)
        return row.value if row else ""
    return {"dnd_start": _get("notify_dnd_start"), "dnd_end": _get("notify_dnd_end")}


@router.put("/dnd")
def set_dnd(payload: dict, db: DbSession, _user: CurrentAdmin):
    """设置免打扰时段（HH:MM，空值清除）。"""
    for key in ("notify_dnd_start", "notify_dnd_end"):
        val = str(payload.get(key, "")).strip()
        row = db.get(Setting, key)
        if row:
            row.value = val
        else:
            db.add(Setting(key=key, value=val))
    db.commit()
    return {"ok": True}
