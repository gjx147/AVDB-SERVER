"""统一日志配置：业务模块日志独立文件，app.log 只保留系统信息。

设计：
- 各业务前缀的 logger 挂独立 FileHandler（data/<name>.log），propagate=False
  （不写 app.log，避免混杂）
- 其余 avdb.* / uvicorn 仍写 app.log（系统级日志）
- 白名单式：LOG_MODULES 定义前缀 → 文件映射，未命中的走 app.log
- 幂等：模块重复 import 不会重复挂 handler
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_configured = False


def module_log_config() -> dict[str, str]:
    """业务模块前缀 → 独立日志文件名。"""
    return {
        "avdb.ai": "ai.log",                 # AI 对话/报告/标签/翻译（agent_service/ai_service/ai_*）
        "avdb.new_works": "subscriptions.log",   # 订阅上新监控
        "avdb.subscription_monitor": "subscriptions.log",
        "avdb.media_server": "emby_sync.log",    # Emby（含全量对比）
        "avdb.organizer": "organize.log",        # 文件整理
        "avdb.downloaders": "downloaders.log",   # 下载器
        "avdb.download_tracker": "downloaders.log",
        "avdb.drive115": "downloaders.log",
        "avdb.download_strategy": "downloaders.log",
        "avdb.actor_profile_sync": "actor_profile.log",
        "avdb.actor_profile": "actor_profile.log",
        "avdb.actor_profile_batch": "actor_profile.log",
        "avdb.tag_translate": "ai.log",
        "avdb.crawl": "crawl_console.log",       # 爬虫控制台（含定时爬取调度）
        "avdb.auto_crawl": "crawl_console.log",
        "avdb.ranking_auto_crawl": "crawl_console.log",
        "avdb.auto_retry": "crawl_console.log",
        "avdb.aggregator": "crawl_console.log",
        "avdb.browser_pool": "crawl_console.log",
        "avdb.flaresolverr": "crawl_console.log",
        "avdb.magnet_sources": "magnet.log",     # 磁力搜索
        "avdb.rule_engine": "rule_engine.log",   # 自动规则引擎
        "avdb.filter": "rule_engine.log",
    }


def setup_module_logs(data_dir: Path, level: int = logging.INFO) -> None:
    """为各业务模块挂独立 FileHandler（幂等）。"""
    global _configured
    if _configured:
        return
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    # 按“文件名”聚合，同一文件的多个前缀只挂一个 handler 的副本
    file_handlers: dict[str, RotatingFileHandler] = {}

    for prefix, filename in module_log_config().items():
        lg = logging.getLogger(prefix)
        if filename not in file_handlers:
            fh = RotatingFileHandler(data_dir / filename, maxBytes=10 * 1024 * 1024,
                                     backupCount=3, encoding="utf-8")
            fh.setFormatter(fmt)
            fh.setLevel(level)
            file_handlers[filename] = fh
        lg.addHandler(file_handlers[filename])
        lg.setLevel(level)
        lg.propagate = False  # 不再写 app.log（app.log 只留系统）

    _configured = True


# ---- 可写日志文件白名单（爬取控制台查看器用；文件名 → 展示名） ----
LOG_FILES = {
    "app.log": "系统日志",
    "ai.log": "AI 对话与报告",
    "subscriptions.log": "订阅上新",
    "crawl_console.log": "爬虫调度",
    "magnet.log": "磁力搜索",
    "downloaders.log": "下载器",
    "organize.log": "文件整理",
    "actor_profile.log": "演员资料",
    "actor_works_batch.log": "全部补齐",
    "emby_sync.log": "Emby 同步",
    "scraper_stderr.log": "爬虫子进程",
    "scraper_actor_crawl.log": "爬虫补齐子进程",
}
