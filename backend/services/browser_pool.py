"""Playwright 浏览器实例池 —— 复用 Chromium 防止进程泄漏。

供 backend 进程内的异步任务（data_aggregator / new_works_monitor 等）使用。
scraper 子进程有自己的浏览器管理，不走此池。

设计要点（根治 AVDB hires_images.py 的 Chromium 泄漏 P0）：
- 单例 Browser，多次 new_context 复用，context 用完即关
- 信号量限并发（防止同时开太多 context 耗内存）
- async 上下文管理器（async with acquire() as ctx:），保证异常也释放
- 优雅关闭（lifespan shutdown 时 close）
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from config import get_settings

logger = logging.getLogger("avdb.browser_pool")

_MAX_CONCURRENCY = 2  # 同时最多 2 个 context（NAS 内存有限）


def _get_proxy_from_db() -> str:
    """从 DB settings 表读 http_proxy（用户在设置页填的代理）。"""
    try:
        from database import SessionLocal
        from models import Setting
        db = SessionLocal()
        try:
            row = db.get(Setting, "http_proxy")
            return (row.value or "").strip() if row and row.value else ""
        finally:
            db.close()
    except Exception:
        return ""


class BrowserPool:
    """Playwright 异步浏览器池（单例）。"""

    def __init__(self) -> None:
        self._pw = None
        self._browser: Browser | None = None
        self._sem = asyncio.Semaphore(_MAX_CONCURRENCY)
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """启动 Playwright + 浏览器（在 lifespan startup 调用）。

        架构修复：用 self._lock 防止并发 acquire() 各自 launch。
        """
        if self._browser:
            return
        async with self._lock:
            if self._browser:  # 双重检查
                return
            settings = get_settings()
            logger.info("启动浏览器池…")
            self._pw = await async_playwright().start()
            launch_args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            # 代理：优先从 DB settings 表读（用户在设置页填的），回退到环境变量
            # 对齐 scraper.py 的 proxy 处理：解析 URL + 双保险（launch proxy + --proxy-server arg）
            proxy_url = _get_proxy_from_db() or settings.HTTP_PROXY or ""
            proxy_arg = {}
            if proxy_url:
                from urllib.parse import urlparse
                if proxy_url.startswith("http://") or proxy_url.startswith("https://"):
                    parsed = urlparse(proxy_url)
                    server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
                    proxy_arg = {"server": server}
                    if parsed.username:
                        proxy_arg["username"] = parsed.username
                    if parsed.password:
                        proxy_arg["password"] = parsed.password
                else:
                    # 裸地址 host:port
                    server = f"http://{proxy_url}"
                    proxy_arg = {"server": server}
                # 双保险：launch 级 proxy + Chromium 原生 --proxy-server 参数
                launch_args.append(f"--proxy-server={proxy_arg['server']}")
                logger.info(f"浏览器池使用代理: {proxy_arg['server']}")
            # 用完整 chromium（channel="chromium"），跳过 headless_shell 检测。
            self._browser = await self._pw.chromium.launch(
                headless=True, channel="chromium", args=launch_args, **proxy_arg
            )
            logger.info("浏览器池就绪")

    async def stop(self) -> None:
        """关闭浏览器 + Playwright（在 lifespan shutdown 调用）。"""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None
        logger.info("浏览器池已关闭")

    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator[BrowserContext, None]:
        """获取一个 BrowserContext，用完自动关闭。

        用法：
            async with browser_pool.acquire() as ctx:
                page = await ctx.new_page()
                await page.goto(url)
        """
        if not self._browser:
            await self.start()
        async with self._sem:
            ctx = await self._browser.new_context(  # type: ignore[union-attr]
                locale="en-US",
                timezone_id="America/New_York",
                viewport={"width": 1920, "height": 1080},
            )
            try:
                yield ctx
            finally:
                try:
                    await ctx.close()
                except Exception as e:
                    logger.warning(f"关闭 context 失败: {e}")

    async def fetch_html(self, url: str, timeout: int = 30000, wait_until: str = "domcontentloaded") -> str:
        """便捷方法：用池里的浏览器抓取一个 URL 的 HTML。"""
        async with self.acquire() as ctx:
            page: Page = await ctx.new_page()
            try:
                await page.goto(url, timeout=timeout, wait_until=wait_until)
                await page.wait_for_timeout(1000)  # 等 JS 渲染
                return await page.content()
            finally:
                await page.close()


# 单例
browser_pool = BrowserPool()
