"""APScheduler 统一调度中心。

取代 AVDB 的 5 个手写 threading+sleep 循环。
所有周期任务（auto_crawl / 订阅巡检 / 新作品监控 / 月报 / 下载轮询）挂这里。

设计要点：
- AsyncIOScheduler（配合 uvicorn 事件循环）
- lifespan 启停（不在 import-time 启动）
- 动态注册/移除 job（设置变更时重排）
- misfire_grace_time + coalesce（错过的合并执行，不堆积）
- jobstore 持久化到 SQLite（抗重启，但 FastAPI 进程内调度通常够用，先用内存）
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger("avdb.scheduler")

# 全局单例（lifespan 启动时创建，shutdown 时关闭）
_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    """获取调度器单例（未启动时自动创建）。"""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(
            job_defaults={
                "coalesce": True,          # 错过多次只执行一次
                "max_instances": 1,        # 同一 job 不并发
                "misfire_grace_time": 300,  # 5 分钟内的 misfire 仍执行
            },
            timezone="Asia/Shanghai",
        )
    return _scheduler


async def start_scheduler() -> None:
    """启动调度器（在 lifespan startup 调用）。"""
    sched = get_scheduler()
    if not sched.running:
        sched.start()
        logger.info("调度中心已启动 (jobs=%d)", len(sched.get_jobs()))


async def stop_scheduler() -> None:
    """关闭调度器（在 lifespan shutdown 调用）。"""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("调度中心已关闭")
    _scheduler = None


def add_interval_job(
    func: Callable[..., Any],
    job_id: str,
    seconds: int,
    *,
    replace_existing: bool = True,
    **kwargs: Any,
) -> None:
    """注册一个按固定间隔执行的 job。"""
    sched = get_scheduler()
    sched.add_job(
        func,
        trigger=IntervalTrigger(seconds=seconds),
        id=job_id,
        replace_existing=replace_existing,
        kwargs=kwargs,
    )
    logger.info("注册间隔任务 %s (每 %ds)", job_id, seconds)


def add_cron_job(
    func: Callable[..., Any],
    job_id: str,
    *,
    hour: int = 0,
    minute: int = 0,
    day: int | None = None,
    replace_existing: bool = True,
    **kwargs: Any,
) -> None:
    """注册一个 cron 定时 job（如每月1号、每天某时）。"""
    sched = get_scheduler()
    cron_kwargs: dict[str, Any] = {"hour": hour, "minute": minute}
    if day is not None:
        cron_kwargs["day"] = day
    sched.add_job(
        func,
        trigger=CronTrigger(**cron_kwargs),
        id=job_id,
        replace_existing=replace_existing,
        kwargs=kwargs,
    )
    logger.info("注册 cron 任务 %s (%s)", job_id, cron_kwargs)


def remove_job(job_id: str) -> bool:
    """移除一个 job。返回是否实际移除了。"""
    sched = get_scheduler()
    try:
        sched.remove_job(job_id)
        logger.info("移除任务 %s", job_id)
        return True
    except Exception:
        return False


def list_jobs() -> list[dict]:
    """列出所有已注册的 job（供 dashboard 查看）。"""
    sched = get_scheduler()
    jobs = []
    for j in sched.get_jobs():
        jobs.append(
            {
                "id": j.id,
                "next_run": str(j.next_run_time) if j.next_run_time else None,
                "trigger": str(j.trigger),
            }
        )
    return jobs
