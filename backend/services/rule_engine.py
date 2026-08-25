"""自动化规则引擎（N22）：对新入库/待处理任务按条件求值，执行动作。"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

logger = logging.getLogger("avdb.rule_engine")


def _match(task, cond: dict) -> bool:
    """任务是否满足条件。"""
    if cond.get("actor"):
        t_actors = set(a.strip() for a in (task.actors or "").split(",") if a.strip())
        if not t_actors.intersection(set(cond["actor"])):
            return False
    if cond.get("tag"):
        t_tags = set(a.strip() for a in (task.tags or "").split(",") if a.strip())
        if not t_tags.intersection(set(cond["tag"])):
            return False
    if cond.get("maker") and (task.maker or "").strip() not in cond["maker"]:
        return False
    if cond.get("rating_min") is not None and (task.rating or 0) < float(cond["rating_min"]):
        return False
    if cond.get("is_new") and task.created_at:
        if task.created_at < datetime.utcnow() - timedelta(hours=2):
            return False
    return True


async def _execute_actions(task, actions: dict, db=None) -> list[str]:
    """执行动作，返回执行的列表。db 为空时自行开 session（打磨：复用调用方 session 减少连接开销）。"""
    from models import Task

    done: list[str] = []
    acts = actions.get("actions") or []
    own_db = db is None
    if own_db:
        from database import SessionLocal
        db = SessionLocal()
    try:
        t = db.get(Task, task.id)
        if not t:
            return done
        if "favorite" in acts and not t.is_favorite:
            t.is_favorite = True
            from datetime import datetime as _dt
            t.favorite_at = _dt.utcnow()
            db.commit()
            done.append("favorite")
    finally:
        if own_db:
            db.close()
    if "notify" in acts:
        try:
            from services.notifier import notify
            note = actions.get("note") or f"命中规则：{task.video_code}"
            await notify("new_works", f"规则命中 {task.video_code}", note)
            done.append("notify")
        except Exception as e:
            logger.warning("规则通知失败: %s", e)
    if "push" in acts and task.best_magnet:
        try:
            from services.download_strategy import push_with_strategy
            r = await push_with_strategy(task.id)
            if r.get("ok"):
                done.append("push")
        except Exception as e:
            logger.warning("规则推送失败: %s", e)
    return done


async def evaluate_all() -> dict:
    """对所有已启用规则，匹配最近 2 小时入库的任务并执行动作。"""
    from database import SessionLocal
    from models import Rule, Task

    db = SessionLocal()
    try:
        rules = db.execute(select(Rule).where(Rule.enabled == True)).scalars().all()  # noqa: E712
        if not rules:
            return {"ok": True, "rules": 0, "hits": 0}
        since = datetime.utcnow() - timedelta(hours=2)
        candidates = db.execute(
            select(Task).where(Task.created_at >= since)
        ).scalars().all()
        hits = 0
        for rule in rules:
            try:
                cond = json.loads(rule.conditions_json or "{}") or {}
            except Exception:
                cond = {}
            try:
                actions = json.loads(rule.actions_json or "{}") or {}
            except Exception:
                actions = {}
            matched = [t for t in candidates if _match(t, cond)]
            executed = 0
            for t in matched[:20]:
                await _execute_actions(t, actions, db)
                executed += 1
                hits += 1
            if matched:
                # 打磨：计数与实际执行数一致（原实现把未执行的匹配也计入）
                rule.hit_count += executed
                rule.last_run_at = datetime.utcnow()
        db.commit()
        return {"ok": True, "rules": len(rules), "hits": hits}
    finally:
        db.close()
