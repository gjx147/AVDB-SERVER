"""存量自动入库：auto_add=true 演员名下未在 Emby 的任务自动提取+推送（不限新旧）。

- 需求：影片库里的作品只要没在库、对应演员订阅开了自动入库 → 自动提取+推送下载
- 去重：已推送过（downloads 表有 pushed/downloading/completed 记录）或本轮已处理则跳过
- 限量：每演员每轮最多 N 部（防风暴）；全部走 _trigger_extract_and_push 现有链路
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

logger = logging.getLogger("avdb.auto_ingest")


def _candidates_for_actor(db, actor_id: int, per_actor_limit: int) -> list:
    """该演员名下：有番号、不在 Emby（含未知）、无进行中/已完成下载的任务。"""
    from models import Task, actor_movies, Download

    rows = db.execute(
        select(Task)
        .join(actor_movies, actor_movies.c.task_id == Task.id)
        .where(actor_movies.c.actor_id == actor_id, Task.video_code.isnot(None))
        .order_by(Task.id.desc())
        .limit(300)
    ).scalars().all()

    # 已推送/推送中/已完成下载的跳过（downloads 表有记录即视为已处理）
    pushed_ids = set(db.execute(
        select(Download.task_id).where(Download.task_id.in_([t.id for t in rows]),
                                       Download.status.in_(["pushed", "downloading", "completed"]))
    ).scalars().all())

    out = []
    for t in rows:
        if t.id in pushed_ids:
            continue
        if t.media_in_library:  # 已确认在库的不推
            continue
        out.append(t)
        if len(out) >= per_actor_limit:
            break
    return out


async def run_auto_ingest_cycle(per_actor_limit: int = 5, max_actors: int = 20) -> dict:
    """存量自动入库一轮：auto_add 演员名下未在库任务 → 提取+推送。"""
    from models import Actor, Subscription
    from database import SessionLocal
    db = SessionLocal()
    try:
        actor_ids = db.execute(
            select(Subscription.actor_id)
            .where(Subscription.sub_type == "actor", Subscription.auto_add == True,  # noqa: E712
                   Subscription.enabled == True, Subscription.actor_id.isnot(None))  # noqa: E712
            .limit(max_actors)
        ).scalars().all()
        names = {a.id: a.name for a in db.execute(select(Actor).where(Actor.id.in_(actor_ids))).scalars().all()}
    finally:
        db.close()
    if not actor_ids:
        return {"ok": True, "actors": 0, "extracted": 0, "pushed": 0}

    from services.new_works_monitor import _trigger_extract_and_push

    extracted = 0
    details = []
    for aid in actor_ids:
        try:
            cands = _candidates_for_actor(SessionLocal(), aid, per_actor_limit)
        except Exception as e:
            logger.warning("存量入库候选查询失败(actor %s): %s", aid, e)
            continue
        for t in cands[:per_actor_limit]:
            try:
                await _trigger_extract_and_push(t.id, t.video_code or f"#{t.id}")
                extracted += 1
                details.append(f"{t.video_code}")
            except Exception as e:
                logger.warning("存量入库触发失败(task %s): %s", t.id, e)
    return {"ok": True, "actors": len(actor_ids), "extracted": extracted, "details": details}
