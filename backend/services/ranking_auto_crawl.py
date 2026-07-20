"""排行榜自动爬取调度服务。

读 DB settings:
- ranking_auto_crawl: 是否启用
- ranking_auto_interval_hours: 爬取间隔（小时）
- ranking_types: 爬取哪些榜单（逗号分隔: daily,weekly,monthly,actor）

被 APScheduler 按间隔调用，触发 scraper ranking 子命令。
"""

from __future__ import annotations

import logging

from database import SessionLocal
from models import Setting

logger = logging.getLogger("avdb.ranking_auto_crawl")


def _get_setting(db, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row and row.value else default


async def run_ranking_crawl_cycle() -> dict:
    """定时爬取排行榜。"""
    db = SessionLocal()
    try:
        enabled = _get_setting(db, "ranking_auto_crawl", "false").lower() == "true"
        if not enabled:
            return {"ok": False, "message": "排行榜自动爬取未启用"}

        types_str = _get_setting(db, "ranking_types", "daily")
        rank_types = [t.strip() for t in types_str.split(",") if t.strip()]

        if not rank_types:
            return {"ok": False, "message": "未配置排行类型"}

        logger.info("排行榜自动爬取: %s", rank_types)

        # 检查全局锁：手动触发的 scraper 在跑则跳过
        from services import scraper_lock
        if scraper_lock.is_running():
            logger.warning("手动爬取进行中，跳过自动排行爬取")
            return {"ok": False, "message": "手动爬取进行中"}

        # 逐个触发 ranking 爬取
        from services.auto_crawl import _run_scraper
        results = []
        for rt in rank_types:
            ok = await _run_scraper(["ranking", "--rank-type", rt, "--max-pages", "5"])
            results.append({"type": rt, "ok": ok})

        return {"ok": True, "results": results}
    finally:
        db.close()


def register_job(interval_hours: int = 24) -> None:
    """注册到调度中心。"""
    from services.scheduler import add_interval_job
    add_interval_job(run_ranking_crawl_cycle, "ranking-auto-crawl", seconds=interval_hours * 3600)
    logger.info("ranking_auto_crawl 已注册: 每 %dh", interval_hours)
