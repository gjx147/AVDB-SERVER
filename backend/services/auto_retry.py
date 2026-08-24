"""自动重试调度服务 —— 定时重试失败任务。

读 DB settings:
- auto_retry_enabled: 是否启用
- auto_retry_interval: 重试间隔（秒）
- auto_retry_max_count: 最大重试次数

被 APScheduler 按间隔调用：查询 status=failed 且 retry_count < max_count 的任务
（仅作门槛判断，不重置状态），按出现失败任务的列表源逐个触发
scraper extract --list-source-id N --failed-only（与 auto_crawl.run_extract_cycle 一致）。
任务保持 failed，extract 成功处理后才由 scraper 转 visited；失败则 mark_failed
且 retry_count+1。DB 的 auto_retry_interval 变化时动态重排调度间隔。
"""

from __future__ import annotations

import logging
import os

from database import SessionLocal
from models import Setting, Task

logger = logging.getLogger("avdb.auto_retry")

# 当前注册到调度中心的间隔（秒）。run_retry_cycle 读到 DB 的
# auto_retry_interval 与此不一致时动态重排，让 DB 配置即时生效。
_last_interval: int | None = None


def _get_setting(db, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row and row.value else default


async def run_retry_cycle() -> dict:
    """检查失败任务并重试。"""
    db = SessionLocal()
    try:
        enabled = _get_setting(db, "auto_retry_enabled", "false").lower() == "true"
        if not enabled:
            return {"ok": False, "message": "自动重试未启用"}

        max_count = int(_get_setting(db, "auto_retry_max_count", "3"))
        interval = int(_get_setting(db, "auto_retry_interval", "300"))

        # DB 的 auto_retry_interval 优先于注册时 env 值：不一致则动态重排 job
        # （scheduler 提供 remove_job/add_interval_job，replace_existing 幂等，改动可控）
        global _last_interval
        if _last_interval != interval:
            from services.scheduler import add_interval_job
            add_interval_job(run_retry_cycle, "auto-retry", seconds=interval, replace_existing=True)
            _last_interval = interval
            logger.info("自动重试: 调度间隔按 DB settings 更新为 %ds", interval)

        # 查找存在可重试失败任务的列表源（只做门槛判断，不重置状态）：
        # extract --failed-only 消费的就是 status='failed' 队列
        # （scraper.extract_magnets -> store.get_failed_urls），任务保持 failed，
        # 处理成功后才由 scraper 转 visited；失败则 mark_failed 且 retry_count+1。
        from sqlalchemy import select, and_
        failed_source_ids = db.execute(
            select(Task.list_source_id).where(
                and_(
                    Task.status == "failed",
                    Task.retry_count < max_count,
                )
            ).distinct()
        ).scalars().all()

        if not failed_source_ids:
            logger.info("自动重试: 无失败任务需要重试")
            return {"ok": True, "retried": 0}

        logger.info(
            "自动重试: %d 个列表源存在可重试失败任务 (max_count=%d)",
            len(failed_source_ids), max_count,
        )

        # 触发 scraper extract（非阻塞，不等完成）。
        # 注意：extract 不带 --list-source-id 会走文件模式（读 PENDING_URLS_FILE，
        # 完全不碰 DB 的 failed 队列），因此必须按源逐个触发，与 auto_crawl.run_extract_cycle 对齐。
        try:
            from services import scraper_lock
            if scraper_lock.is_running():
                logger.warning("自动重试: 已有爬取在运行，跳过 extract 触发")
            else:
                from services.auto_crawl import _run_scraper
                import asyncio

                async def _retry_failed_serially():
                    # 串行触发（全局爬取锁同一时刻只允许一个 scraper，
                    # 并发触发只会被锁拒绝并自灭，串行避免浪费）
                    for sid in failed_source_ids:
                        await _run_scraper(
                            ["extract", "--list-source-id", str(sid), "--failed-only"]
                        )

                asyncio.create_task(_retry_failed_serially())
        except Exception as e:
            logger.warning("自动重试: 触发 extract 失败: %s", e)

        return {"ok": True, "retried": len(failed_source_ids)}
    finally:
        db.close()


def register_job(interval: int = 300) -> None:
    """注册到调度中心。"""
    global _last_interval
    from services.scheduler import add_interval_job
    add_interval_job(run_retry_cycle, "auto-retry", seconds=interval)
    _last_interval = interval
    logger.info("auto_retry 已注册: 每 %ds 检查失败任务", interval)
