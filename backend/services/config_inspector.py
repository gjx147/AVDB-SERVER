"""配置巡检服务（G2）——定时检查配置健康，发现问题推送通知。"""
from __future__ import annotations

import logging

logger = logging.getLogger("avdb.config_inspector")


def run_inspection() -> dict:
    """跑一遍静态检查器（不依赖 AI），返回 problems/tips。"""
    from services.agent_service import _inspect
    from database import SessionLocal
    db = SessionLocal()
    try:
        return _inspect(db, {})
    finally:
        db.close()


async def weekly_inspection_job() -> dict:
    """每周巡检：发现问题（error 级）推送通知。"""
    try:
        result = run_inspection()
        problems = result.get("problems", [])
        errors = [p for p in problems if p.get("level") == "error"]
        if not errors:
            logger.info("周巡检通过：无 error 级问题")
            return {"ok": True, "problems": 0}
        from services.notifier import notify
        lines = "\n".join(f"- {p['item']}：{p['detail']}" for p in errors)
        await notify("system", "配置巡检发现异常", lines)
        logger.warning("周巡检发现问题 %d 项", len(errors))
        return {"ok": True, "problems": len(errors), "items": errors}
    except Exception as e:
        logger.error("周巡检失败: %s", e)
        return {"ok": False, "message": str(e)}
