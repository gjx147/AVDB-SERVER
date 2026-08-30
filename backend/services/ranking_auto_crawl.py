"""排行榜自动爬取调度服务。

读 DB settings:
- ranking_auto_crawl: 是否启用（默认 true）
- ranking_types: 爬取哪些榜单（逗号分隔，默认按 日榜→月榜→周榜→演员月榜 顺序串行）
- ranking_auto_cron_hour: 每天几点执行（也可用环境变量 RANKING_AUTO_CRON_HOUR 覆盖）

由 APScheduler 每天定点触发，逐个调用 scraper ranking 子命令（串行，失败不中断后续榜单）。
"""

from __future__ import annotations

import logging
import os

from database import SessionLocal
from models import Setting

logger = logging.getLogger("avdb.ranking_auto_crawl")

# 默认爬取顺序：日榜 → 月榜 → 周榜 → 演员月榜
DEFAULT_RANK_TYPES = "daily,monthly,weekly,actor"


def _get_setting(db, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row and row.value else default


async def run_ranking_crawl_cycle() -> dict:
    """每天定点串行爬取排行榜（日→月→周→演员月榜，单个失败不中断）。"""
    import time

    db = SessionLocal()
    try:
        enabled = _get_setting(db, "ranking_auto_crawl", "true").lower() == "true"
        if not enabled:
            return {"ok": False, "message": "排行榜自动爬取未启用"}

        rank_types = [t.strip() for t in _get_setting(db, "ranking_types", DEFAULT_RANK_TYPES).split(",") if t.strip()]
        if not rank_types:
            return {"ok": False, "message": "未配置排行类型"}

        logger.info("排行榜自动爬取开始，顺序: %s", rank_types)

        # 检查全局锁：手动触发的 scraper 在跑则跳过本轮（下个定时点再试）
        from services import scraper_lock
        if scraper_lock.is_running(scraper_lock.CHANNEL_MAIN):
            logger.warning("main 通道爬取进行中，跳过本轮自动排行爬取")
            return {"ok": False, "message": "爬取进行中"}

        # 串行逐个触发（日→月→周→演员月榜），失败不中断后续榜单
        from services.auto_crawl import _run_scraper
        results = []
        for rt in rank_types:
            t0 = time.monotonic()
            try:
                ok = await _run_scraper(["ranking", "--rank-type", rt, "--max-pages", "5"])
            except Exception as e:
                logger.error("榜单 %s 自动爬取异常: %s", rt, e)
                ok = False
            cost = round(time.monotonic() - t0, 1)
            results.append({"type": rt, "ok": ok, "seconds": cost})
            logger.info("榜单 %s 自动爬取%s（%.1fs）", rt, "成功" if ok else "失败", cost)

        ok_count = sum(1 for r in results if r["ok"])
        logger.info("排行榜自动爬取完成: %d/%d 个榜单成功", ok_count, len(results))
        return {"ok": True, "results": results, "ok_count": ok_count}
    finally:
        db.close()


def register_job(hour: int | None = None) -> None:
    """注册到调度中心：每天定点执行（默认 5:00，可用 RANKING_AUTO_CRON_HOUR 环境变量覆盖）。"""
    from services.scheduler import add_cron_job
    if hour is None:
        try:
            hour = int(os.environ.get("RANKING_AUTO_CRON_HOUR", "5"))
        except ValueError:
            hour = 5
    add_cron_job(run_ranking_crawl_cycle, "ranking-auto-crawl", hour=hour, minute=0)
    logger.info("ranking_auto_crawl 已注册: 每天 %02d:00（日→月→周→演员月榜 串行）", hour)
