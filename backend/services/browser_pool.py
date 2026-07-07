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
            proxy_arg = {}
            if settings.HTTP_PROXY:
                proxy_arg = {"server": settings.HTTP_PROXY}
            self._browser = await self._pw.chromium.launch(args=launch_args, **proxy_arg)
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
