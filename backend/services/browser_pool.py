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
            # proxy 必须作为 launch() 的 proxy= 关键字参数（一个 dict），不能展开成顶层参数
            launch_kwargs: dict = {"headless": True, "channel": "chromium", "args": launch_args}
            if proxy_arg:
                launch_kwargs["proxy"] = proxy_arg
            self._browser = await self._pw.chromium.launch(**launch_kwargs)
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
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                locale="en-US",
                timezone_id="America/New_York",
                viewport={"width": 1920, "height": 1080},
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            try:
                yield ctx
            finally:
                try:
                    await ctx.close()
                except Exception as e:
                    logger.warning(f"关闭 context 失败: {e}")

    async def fetch_html(self, url: str, timeout: int = 30000, wait_until: str = "domcontentloaded") -> str:
        """便捷方法：用池里的浏览器抓取一个 URL 的 HTML。

        对齐 scraper 的 Cloudflare 绕过策略：
        1. 给 page 应用 playwright-stealth 反检测
        2. goto 后等待 Cloudflare 验证完成（title 不再是 "Just a moment"）
        """
        async with self.acquire() as ctx:
            page: Page = await ctx.new_page()
            try:
                # 应用 stealth 反检测（async API）
                try:
                    from playwright_stealth import stealth_async
                    await stealth_async(page)
                except ImportError:
                    logger.warning("playwright-stealth 未安装，跳过反检测")
                except Exception as e:
                    logger.warning(f"stealth_async 应用失败: {e}")

                await page.goto(url, timeout=timeout, wait_until=wait_until)

                # 检测并等待 Cloudflare 验证完成（最多等 30 秒）
                # Cloudflare 验证页的 title 是 "Just a moment..."
                for i in range(30):
                    title = await page.title()
                    if "just a moment" not in title.lower():
                        logger.info(f"Cloudflare 验证通过（等待 {i} 秒）")
                        break
                    await page.wait_for_timeout(1000)
                else:
                    logger.warning(f"Cloudflare 验证未在 30 秒内完成: {url}")

                await page.wait_for_timeout(1000)  # 等 JS 渲染
                return await page.content()
            finally:
                await page.close()


# 单例
browser_pool = BrowserPool()
