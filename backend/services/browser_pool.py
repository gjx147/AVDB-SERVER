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
import random
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

        对齐 scraper 的完整 Cloudflare 绕过策略：
        1. stealth 反检测
        2. goto 后主动处理 Cloudflare 验证（点击 Turnstile checkbox / iframe）
        3. 年龄验证
        """
        async with self.acquire() as ctx:
            page: Page = await ctx.new_page()
            try:
                # 应用 stealth 反检测
                try:
                    from playwright_stealth import stealth_async
                    await stealth_async(page)
                except ImportError:
                    logger.warning("playwright-stealth 未安装，跳过反检测")
                except Exception as e:
                    logger.warning(f"stealth_async 应用失败: {e}")

                await page.goto(url, timeout=timeout, wait_until=wait_until)
                await asyncio.sleep(2)  # 等页面加载

                # Cloudflare 验证处理（对齐 scraper._handle_security_check）
                await self._handle_cloudflare(page, url)

                return await page.content()
            finally:
                await page.close()

    async def _handle_cloudflare(self, page: Page, url: str) -> None:
        """处理 Cloudflare 验证 + 年龄验证（async 版，对齐 scraper 逻辑）。

        - 检测验证页（title 含 "just a moment" 或 URL 含 challenge）
        - 主动查找并点击 Turnstile checkbox（页面内 + iframe 内）
        - 年龄验证按钮点击
        """
        try:
            title = (await page.title()) or ""
            current_url = page.url

            # 快速判断是否需要验证
            need_check = (
                "just a moment" in title.lower()
                or "challenge" in current_url
                or await self._has_text(page, "Security Verification")
                or await self._has_text(page, "确认您是真人")
            )

            if not need_check:
                # 可能已经通过，检查年龄验证
                await self._handle_age_verification(page)
                return

            logger.info(f"[browser_pool] 检测到 Cloudflare 验证，开始处理: {url}")
            initial_url = current_url
            consecutive_no_element = 0

            for i in range(15):
                logger.debug(f"[browser_pool] 验证处理第 {i+1}/15 次")

                # URL 变化检测
                current_url = page.url
                if "challenge" not in current_url and "challenge" in initial_url:
                    if await self._check_passed(page):
                        logger.info("[browser_pool] Cloudflare 验证通过（URL 变化）")
                        break

                # 查找验证按钮（页面内）
                btn = await self._find_challenge_button(page)

                # 查找 iframe 内的验证按钮
                if not btn:
                    btn = await self._find_challenge_button_in_iframe(page)

                if btn:
                    consecutive_no_element = 0
                    try:
                        logger.info("[browser_pool] 找到验证按钮，点击")
                        await btn.scroll_into_view_if_needed()
                        await asyncio.sleep(0.5)
                        await btn.click(timeout=5000)
                        await asyncio.sleep(random.uniform(5, 8))
                        if await self._check_passed(page):
                            logger.info("[browser_pool] Cloudflare 验证通过（点击后）")
                            break
                    except Exception as e:
                        logger.debug(f"[browser_pool] 点击验证按钮失败: {e}")
                else:
                    consecutive_no_element += 1
                    if consecutive_no_element >= 3 and await self._check_passed(page):
                        logger.info("[browser_pool] Cloudflare 验证自动通过")
                        break
                    await asyncio.sleep(3)
                    if await self._check_passed(page):
                        logger.info("[browser_pool] Cloudflare 验证自动通过")
                        break

                await asyncio.sleep(2)
            else:
                logger.warning(f"[browser_pool] Cloudflare 验证 15 轮未通过: {url}")

            # 年龄验证
            await self._handle_age_verification(page)

        except Exception as e:
            logger.warning(f"[browser_pool] Cloudflare 处理异常: {e}")

    async def _check_passed(self, page: Page) -> bool:
        """检查验证是否通过（对齐 scraper._check_verification_passed）。"""
        try:
            url = page.url
            if "challenge" in url:
                return False
            # 有实际内容就算通过
            if await page.locator("a[href*='/v/']").count() > 0:
                return True
            if await page.locator("a[href^='magnet:']").count() > 0:
                return True
            body = await page.locator("body").inner_text()
            if body and len(body.strip()) > 100:
                return True
            # 无验证文本也算通过
            if not await self._has_text(page, "Security Verification"):
                return True
            return False
        except Exception:
            return False

    async def _has_text(self, page: Page, text: str) -> bool:
        try:
            return await page.locator(f"text={text}").count() > 0
        except Exception:
            return False

    async def _find_challenge_button(self, page: Page):
        """在页面内查找 Cloudflare Turnstile 验证按钮。"""
        selectors = [
            ".ctp-checkbox-label",
            "#challenge-stage",
            "input[type='checkbox']",
            "label[for*='challenge']",
            ".ctp-checkbox",
        ]
        for sel in selectors:
            try:
                elem = page.locator(sel).first
                if await elem.count() > 0 and await elem.is_visible():
                    return elem
            except Exception:
                continue
        # 文本匹配兜底
        for text in ["确认您是真人", "Verify"]:
            try:
                elem = page.locator(f"text={text}").first
                if await elem.count() > 0:
                    return elem
            except Exception:
                continue
        return None

    async def _find_challenge_button_in_iframe(self, page: Page):
        """在 iframe 内查找验证按钮。"""
        try:
            iframes = await page.locator("iframe").all()
            for iframe in iframes:
                try:
                    src = await iframe.get_attribute("src") or ""
                    title = await iframe.get_attribute("title") or ""
                    if "challenge-platform" not in src and "Cloudflare" not in title:
                        continue
                    frame = await iframe.content_frame()
                    if not frame:
                        continue
                    for sel in [".ctp-checkbox-label", "#challenge-stage"]:
                        try:
                            elem = frame.locator(sel).first
                            if await elem.count() > 0:
                                return elem
                        except Exception:
                            continue
                    for text in ["确认您是真人"]:
                        try:
                            elem = frame.locator(f"text={text}").first
                            if await elem.count() > 0:
                                return elem
                        except Exception:
                            continue
                except Exception:
                    continue
        except Exception:
            pass
        return None

    async def _handle_age_verification(self, page: Page) -> None:
        """年龄验证（是,我已滿18歲）。"""
        try:
            btn = page.locator("text=是,我已滿18歲").first
            if await btn.count() > 0:
                logger.info("[browser_pool] 找到年龄验证按钮，点击")
                await btn.click()
                await asyncio.sleep(1)
        except Exception:
            pass


# 单例
browser_pool = BrowserPool()
