"""演员资料自动同步 —— 定时任务：扫 profile_fetched=0 的演员批量抓取双源资料（minnano-av + laoshi）。

注册于 main.py lifespan（与订阅巡检同模式）。每轮限 BATCH_SIZE 个、
演员间 5-10s 限速，防 hammer 两源；失败标记跳过避免反复重试。
"""

from __future__ import annotations

import logging
import random
import time
from typing import Callable

logger = logging.getLogger("avdb.actor_profile_sync")

BATCH_SIZE = 5

_FIELDS = (
    "blood_type", "zodiac", "birthplace", "nationality", "active_years",
    "bio", "timeline", "alias", "birth_date", "height", "cup", "measurements", "debut_date",
    "agency", "hobbies", "debut_work", "twitter", "website", "tags",
    "avatar_url",
)

# 进度钩子：一键提取后台任务通过它汇报当前演员名（其余场景为 None）
_PROGRESS_HOOK: Callable[[str], None] | None = None


def set_progress_hook(hook: Callable[[str], None] | None) -> None:
    global _PROGRESS_HOOK
    _PROGRESS_HOOK = hook


def run_cycle() -> dict:
    """抓取一批待处理演员（最多 BATCH_SIZE 个）。返回统计。

    男演员（gender='male'）不抓取资料：直接标记已处理，避免一直滞留队列。
    """
    from database import SessionLocal
    from models import Actor
    from sqlalchemy import or_, select, update as sa_update
    from services.actor_profile import fetch_profile

    db = SessionLocal()
    try:
        # 男演员跳过：批量标记已处理（不抓取），脱离待抓队列
        male_skipped = db.execute(
            sa_update(Actor)
            .where(Actor.gender == 'male', Actor.profile_fetched.is_(False))
            .values(profile_fetched=True, profile_fetch_failed=False)
        )
        db.commit()
        if male_skipped.rowcount:
            logger.info("跳过男演员资料抓取: %d 位（已标记处理）", male_skipped.rowcount)

        rows = db.execute(
            select(Actor).where(
                Actor.profile_fetched.is_(False),
                Actor.profile_fetch_failed.is_(False),
                or_(Actor.gender.is_(None), Actor.gender != 'male'),
            ).order_by(Actor.id).limit(BATCH_SIZE)
        ).scalars().all()
        if not rows:
            return {"fetched": 0, "skipped": 0}
        done = skipped = 0
        for actor in rows:
            if _PROGRESS_HOOK:
                try:
                    _PROGRESS_HOOK(actor.name)
                except Exception:
                    pass
            try:
                result = fetch_profile(actor.name, actor.name_en)
                if result.get("ok"):
                    # 锁定保护：不覆盖任何手动编辑的资料字段，仅标记已抓取
                    if not actor.profile_locked:
                        for k, v in (result.get("fields") or {}).items():
                            if k in _FIELDS and v:
                                # 头像只填空缺：已有头像（含手动更换的）不覆盖
                                if k == "avatar_url" and actor.avatar_url:
                                    continue
                                setattr(actor, k, v)
                    actor.profile_fetched = True
                    actor.profile_fetch_failed = False
                    db.commit()
                    done += 1
                    logger.info(f"演员资料已抓取 {actor.name}（来源 {result.get('source')}）")
                else:
                    actor.profile_fetch_failed = True
                    db.commit()
                    skipped += 1
                    logger.info(f"演员资料双源未命中: {actor.name}")
            except Exception as e:
                db.rollback()
                skipped += 1
                logger.warning(f"演员资料抓取异常 {actor.name}: {e}")
            # 限速：演员间 5-10 秒
            if rows.index(actor) < len(rows) - 1:
                time.sleep(random.uniform(5, 10))
        return {"fetched": done, "skipped": skipped}
    finally:
        db.close()


def register_job(interval_min: int = 20) -> None:
    """注册到 APScheduler（main.py lifespan 调用）。"""
    from services.scheduler import add_interval_job
    add_interval_job(run_cycle, "actor-profile-sync", seconds=interval_min * 60)
    logger.info("演员资料自动同步已注册: 每 %dmin（每轮 %d 个）", interval_min, BATCH_SIZE)
