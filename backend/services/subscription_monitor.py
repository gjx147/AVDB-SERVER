"""订阅巡检服务 —— 定时检查所有订阅，按类型执行不同逻辑。

订阅类型分发：
- ranking: 调 scraper ranking 子命令同步榜单 + 批量入库
- actor: 委托 new_works_monitor.check_actor_new_works
- composite: 扫描已有任务按 filters 匹配，命中的标记/入库

设计：
- async，挂 APScheduler（默认每 6 小时一轮）
- 遍历 enabled 且到 check_interval 的订阅
- 每个订阅执行后更新 last_checked_at + last_result
- 失败不中断后续订阅
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update, func

from database import SessionLocal
from models import ListSource, Subscription, Task

logger = logging.getLogger("avdb.subscription_monitor")

_state = {"running": False, "last_run": None, "total_checked": 0, "total_matched": 0}


def _parse_filters(filters_json: str | None) -> dict:
    if not filters_json:
        return {}
    try:
        return json.loads(filters_json)
    except Exception:
        return {}


def _task_matches_filters(task: Task, filters: dict) -> bool:
    """检查 task 是否符合 composite 订阅的过滤条件。"""
    # 番号前缀黑名单
    for prefix in filters.get("exclude_codes", []):
        if task.video_code and task.video_code.upper().startswith(prefix.upper()):
            return False
    # makers 白名单
    makers = filters.get("makers", [])
    if makers and task.maker and task.maker not in makers:
        return False
    # labels 白名单
    labels = filters.get("labels", [])
    if labels and task.label and task.label not in labels:
        return False
    # series 白名单
    series = filters.get("series", [])
    if series and task.series and task.series not in series:
        return False
    # genres 白名单（tags 任一命中即可）
    genres = filters.get("genres", [])
    if genres:
        task_tags = set((task.tags or "").split(","))
        if not task_tags.intersection(genres):
            return False
    # 最低评分
    min_rating = filters.get("min_rating")
    if min_rating and (task.rating is None or task.rating < min_rating):
        return False
    # 起始日期
    date_from = filters.get("date_from")
    if date_from and task.release_date and task.release_date < date_from:
        return False
    return True


async def _check_composite(sub: Subscription, db) -> dict:
    """composite 订阅：扫描已有任务，统计匹配数。"""
    filters = _parse_filters(sub.filters_json)
    # 只 SELECT 过滤/更新所需列，避免整行加载 magnets_json/synopsis 等大字段
    tasks = db.execute(
        select(
            Task.id,
            Task.video_code,
            Task.maker,
            Task.label,
            Task.series,
            Task.tags,
            Task.rating,
            Task.release_date,
            Task.view_status,
        ).where(Task.status == "visited")
    ).all()
    matched = [t for t in tasks if _task_matches_filters(t, filters)]
    # auto_add 时把命中的标记为想看（批量 UPDATE，不再依赖 ORM 对象回写）
    added = 0
    if sub.auto_add:
        want_ids = [t.id for t in matched if not t.view_status]
        if want_ids:
            db.execute(
                update(Task)
                .where(
                    Task.id.in_(want_ids),
                    or_(Task.view_status.is_(None), Task.view_status == ""),
                )
                .values(view_status="want")
                .execution_options(synchronize_session=False)
            )
            added = len(want_ids)
    return {
        "type": "composite",
        "scanned": len(tasks),
        "matched": len(matched),
        "marked_want": added,
    }


async def _check_ranking(sub: Subscription, db) -> dict:
    """ranking 订阅：返回待同步提示（实际抓取由 scraper ranking 命令完成）。"""
    return {
        "type": "ranking",
        "rank_type": sub.rank_type,
        "message": "ranking 订阅需配合 scraper ranking 命令同步",
    }


async def _check_actor(sub: Subscription, db) -> dict:
    """actor 订阅：委托 new_works_monitor（含 Emby 比对 + 通知 + auto_add 下载）。"""
    try:
        from services.new_works_monitor import check_actor_new_works

        return await check_actor_new_works(
            sub.actor_id, subscription_id=sub.id, auto_add=sub.auto_add
        )
    except ImportError:
        return {"type": "actor", "message": "new_works_monitor 尚未实现"}
    except Exception as e:
        return {"type": "actor", "error": str(e)}


_DISPATCH = {"ranking": _check_ranking, "actor": _check_actor, "composite": _check_composite}


async def run_check_cycle() -> dict:
    """对所有到期的订阅执行一轮巡检。"""
    if _state["running"]:
        return {"ok": False, "message": "已在运行"}
    _state["running"] = True
    now = datetime.utcnow()
    results = []
    try:
        db = SessionLocal()
        try:
            subs = db.execute(
                select(Subscription).where(Subscription.enabled == True)  # noqa: E712
            ).scalars().all()
        except Exception:
            db.close()
            return {"ok": True, "results": [], "message": "无订阅"}

        total_matched = 0
        for sub in subs:
            # 检查是否到期（按 check_interval_hours）
            if sub.last_checked_at:
                due = sub.last_checked_at + timedelta(hours=sub.check_interval_hours)
                if now < due:
                    continue  # 未到期，跳过
            handler = _DISPATCH.get(sub.sub_type)
            if not handler:
                continue
            try:
                result = await handler(sub, db)
                sub.last_checked_at = now
                sub.last_result = json.dumps(result, ensure_ascii=False)
                total_matched += result.get("matched", 0) + result.get("new_count", 0)
                results.append({"id": sub.id, "name": sub.name, "result": result})
            except Exception as e:
                logger.error(f"订阅 {sub.id}({sub.name}) 巡检失败: {e}")
                sub.last_checked_at = now
                sub.last_result = json.dumps({"error": str(e)})
                results.append({"id": sub.id, "name": sub.name, "error": str(e)})

        # N4: 订阅演员"凉了"提醒——超阈值（默认 180 天，配置 actor_inactive_days）无新作，
        # 合并推送一条通知，避免刷屏
        try:
            _inactive_days = 180
            _cfg_row = db.get(Setting, "actor_inactive_days")
            if _cfg_row and _cfg_row.value:
                try:
                    _inactive_days = int(_cfg_row.value)
                except Exception:
                    pass
            _threshold = now - timedelta(days=_inactive_days)
            _actor_subs = [s for s in subs if s.sub_type == "actor" and s.actor_id]
            if _actor_subs:
                from models import ActorMovie
                _last_rows = db.execute(
                    select(ActorMovie.actor_id, func.max(Task.release_date))
                    .join(Task, Task.id == ActorMovie.task_id)
                    .where(ActorMovie.actor_id.in_([s.actor_id for s in _actor_subs]),
                           Task.release_date.isnot(None))
                    .group_by(ActorMovie.actor_id)
                ).all()
                _last_map = {aid: rd for aid, rd in _last_rows}
                _inactive = []
                for _s in _actor_subs:
                    _rd = _last_map.get(_s.actor_id)
                    if not _rd:
                        continue
                    try:
                        _d = datetime.strptime(str(_rd)[:10], "%Y-%m-%d")
                    except Exception:
                        continue
                    if _d < _threshold:
                        _inactive.append({"name": _s.name, "last": str(_rd)[:10], "days": (now - _d).days})
                if _inactive:
                    _lines = "\n".join(
                        f"· {i['name']}（最后作品 {i['last']}，已 {i['days']} 天）" for i in _inactive[:10]
                    )
                    from services.notifier import notify
                    await notify("actor_inactive", f"{len(_inactive)} 位订阅演员疑似休止/引退", _lines)
        except Exception as e:
            logger.warning("演员休止检测失败: %s", e)
        db.commit()
        _state["last_run"] = now.isoformat()
        _state["total_checked"] = len(results)
        _state["total_matched"] = total_matched
        return {"ok": True, "checked": len(results), "results": results}
    finally:
        _state["running"] = False
        try:
            db.close()
        except Exception:
            pass


def get_state() -> dict:
    return dict(_state)


def register_job(interval_hours: int = 6) -> None:
    """注册到调度中心。"""
    from services.scheduler import add_interval_job

    add_interval_job(run_check_cycle, "subscription-monitor", seconds=interval_hours * 3600)
    logger.info("订阅巡检已注册: 每 %dh", interval_hours)
