"""通知服务 —— Bark/Telegram/Discord/企业微信/Webhook 多通道。

配置存 settings 表（运行时可改），非阻塞 async。
被订阅巡检/新作品监控/月报生成/下载完成调用。

settings 表 key：
- notify_bark_key: Bark 设备 key（完整 URL = https://api.day.app/{key}）
- notify_telegram_token / notify_telegram_chat_id
- notify_discord_webhook: Discord 频道 webhook URL
- notify_wecom_key: 企业微信机器人 key
- notify_webhook_url: 自定义 webhook
- notify_events: 启用的事件（逗号分隔）
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger("avdb.notifier")

# 模块级共享 httpx 客户端（Phase 4：连接复用）
_notify_client: httpx.AsyncClient | None = None


def _get_notify_client() -> httpx.AsyncClient:
    global _notify_client
    if _notify_client is None or _notify_client.is_closed:
        _notify_client = httpx.AsyncClient(timeout=10)
    return _notify_client

ALL_EVENTS = {
    "new_works",       # 检测到新作品
    "subscription",    # 订阅巡检结果
    "download",        # 下载完成
    "monthly_report",  # 月报生成
    "crawl",           # 爬取完成/失败
    "disk_warning",    # 磁盘告警
    "error",           # 错误
}
DEFAULT_EVENTS = {"new_works", "download", "disk_warning"}


async def _get_config() -> dict[str, str]:
    """从 settings 表读通知配置。"""
    from database import SessionLocal
    from models import Setting
    keys = [
        "notify_bark_key", "notify_telegram_token", "notify_telegram_chat_id",
        "notify_discord_webhook", "notify_wecom_key", "notify_webhook_url", "notify_events",
    ]
    db = SessionLocal()
    try:
        result = {}
        for k in keys:
            row = db.get(Setting, k)
            if row and row.value:
                result[k] = row.value
        return result
    finally:
        db.close()


def _event_enabled(event: str, config: dict) -> bool:
    events_str = config.get("notify_events", "")
    if not events_str:
        enabled = DEFAULT_EVENTS
    else:
        enabled = set(events_str.split(","))
    return event in enabled


async def _send_bark(config: dict, title: str, body: str) -> bool:
    key = config.get("notify_bark_key", "").strip()
    if not key:
        return False
    url = f"https://api.day.app/{key}/{quote(title)}/{quote(body)}"
    try:
        c = _get_notify_client()
            r = await c.get(url)
            return r.status_code == 200
    except Exception as e:
        logger.warning(f"Bark 发送失败: {e}")
        return False


async def _send_telegram(config: dict, title: str, body: str) -> bool:
    token = config.get("notify_telegram_token", "").strip()
    chat_id = config.get("notify_telegram_chat_id", "").strip()
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        c = _get_notify_client()
            r = await c.post(url, json={"chat_id": chat_id, "text": f"*{title}*\n{body}", "parse_mode": "Markdown"})
            return r.status_code == 200
    except Exception as e:
        logger.warning(f"Telegram 发送失败: {e}")
        return False


async def _send_discord(config: dict, title: str, body: str) -> bool:
    webhook = config.get("notify_discord_webhook", "").strip()
    if not webhook:
        return False
    try:
        c = _get_notify_client()
            r = await c.post(webhook, json={"content": f"**{title}**\n{body}"})
            return r.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"Discord 发送失败: {e}")
        return False


async def _send_wecom(config: dict, title: str, body: str) -> bool:
    key = config.get("notify_wecom_key", "").strip()
    if not key:
        return False
    url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
    try:
        c = _get_notify_client()
            r = await c.post(url, json={"msgtype": "text", "text": {"content": f"{title}\n{body}"}})
            return r.status_code == 200 and r.json().get("errcode") == 0
    except Exception as e:
        logger.warning(f"企业微信发送失败: {e}")
        return False


async def _send_webhook(config: dict, title: str, body: str, event: str) -> bool:
    url = config.get("notify_webhook_url", "").strip()
    if not url:
        return False
    try:
        c = _get_notify_client()
            r = await c.post(url, json={"event": event, "title": title, "body": body})
            return r.status_code < 400
    except Exception as e:
        logger.warning(f"Webhook 发送失败: {e}")
        return False


async def notify(event: str, title: str, body: str = "") -> dict[str, bool]:
    """发送通知到所有已配置且事件启用的通道。返回各通道结果。"""
    config = await _get_config()
    if not _event_enabled(event, config):
        return {"skipped": True}

    results = await _send_all(config, event, title, body)
    logger.info("通知 [%s] %s -> %s", event, title, results)
    return results


async def _send_all(config: dict, event: str, title: str, body: str) -> dict[str, bool]:
    """实际发送（供 test_notify 复用，不检查 event 过滤）。"""
    import asyncio
    bark, tg, dc, wecom, hook = await asyncio.gather(
        _send_bark(config, title, body),
        _send_telegram(config, title, body),
        _send_discord(config, title, body),
        _send_wecom(config, title, body),
        _send_webhook(config, title, body, event),
    )
    return {"bark": bark, "telegram": tg, "discord": dc, "wecom": wecom, "webhook": hook}


async def test_notify() -> dict[str, bool]:
    """测试通知（发送测试消息到所有已配置通道，不过滤事件）。"""
    config = await _get_config()
    return await _send_all(config, "test", "AVDB-SERVER 测试通知", "如果你收到这条消息，说明通知配置正常。")
