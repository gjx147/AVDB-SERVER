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

# 爬取僵尸进程 watchdog 间隔（秒）：主动回收超时/僵死的 scraper 子进程，
# 不依赖前端轮询 crawl_status 的懒触发（F09）。
_WATCHDOG_INTERVAL = 60

# 全局单例（lifespan 启动时创建，shutdown 时关闭）
_scheduler: AsyncIOScheduler | None = None
# T12: 停止标记——stop 后再次 get_scheduler() 创建新实例时自动启动，
# 避免 job 注册到永不启动的调度器上静默不执行
_stopped = False


def _watchdog_reap_crawl() -> None:
    """回收超时/僵死的爬取进程（watchdog job 回调）。

    函数内 import：routers.crawl 依赖 services.scraper_lock 等模块，
    虽然不反向依赖本模块（无循环依赖），惰性导入可彻底避免 import 顺序耦合。
    """
    try:
        from routers.crawl import reap_timed_out_crawl
        result = reap_timed_out_crawl()
        if result.get("reaped"):
            logger.warning("watchdog: 已回收超时爬取进程")
    except Exception as e:
        logger.warning("watchdog: 回收爬取进程失败: %s", e)


def get_scheduler() -> AsyncIOScheduler:
    """获取调度器单例（未启动时自动创建）。"""
    global _scheduler, _stopped
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(
            job_defaults={
                "coalesce": True,          # 错过多次只执行一次
                "max_instances": 1,        # 同一 job 不并发
                "misfire_grace_time": 300,  # 5 分钟内的 misfire 仍执行
            },
            timezone="Asia/Shanghai",
        )
        if _stopped:
            # T12: 停止后重建的实例自动启动，保证新注册的 job 真正执行
            _stopped = False
            _scheduler.start()
            logger.warning("调度器已停止后重新创建，已自动启动")
    return _scheduler


async def start_scheduler() -> None:
    """启动调度器（在 lifespan startup 调用）。"""
    sched = get_scheduler()
    if not sched.running:
        # 爬取僵尸进程 watchdog：每 60s 主动回收超时进程（F09）。
        # job_defaults 已含 coalesce=True + max_instances=1；replace_existing 防重复注册。
        add_interval_job(_watchdog_reap_crawl, "crawl-reap-watchdog", seconds=_WATCHDOG_INTERVAL)

        # F4: 每周一 9:00 推送本周新作 Top10（需在通知设置启用 weekly_report 事件）
        from services.weekly_report import run_weekly_report
        add_cron_job(run_weekly_report, "weekly-report", day_of_week="mon", hour=9, minute=0)

        # N15: 每日 2:00 评分快照（趋势分析数据源）
        from services.rating_snapshot import run_snapshot
        add_cron_job(run_snapshot, "rating-snapshot", hour=2, minute=0)

        # N22: 每小时规则引擎求值
        from services.rule_engine import evaluate_all
        add_interval_job(evaluate_all, "rule-engine", seconds=3600)
        sched.start()
        logger.info("调度中心已启动 (jobs=%d)", len(sched.get_jobs()))


async def stop_scheduler() -> None:
    """关闭调度器（在 lifespan shutdown 调用）。"""
    global _scheduler, _stopped
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("调度中心已关闭")
    _scheduler = None
    _stopped = True


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
    day_of_week: str | None = None,
    replace_existing: bool = True,
    **kwargs: Any,
) -> None:
    """注册一个 cron 定时 job（如每月1号、每周一某时）。"""
    sched = get_scheduler()
    cron_kwargs: dict[str, Any] = {"hour": hour, "minute": minute}
    if day is not None:
        cron_kwargs["day"] = day
    if day_of_week is not None:
        cron_kwargs["day_of_week"] = day_of_week
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
