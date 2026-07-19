#!/usr/bin/env python3
"""磁力链接爬虫 - 使用 Playwright + playwright-stealth 绕过 Cloudflare 并提取磁力链接；支持 DB 与文件两种存储"""

import argparse
import atexit
import json
import logging
import os
import platform
import random
import shutil
import sys
import time
import traceback
from typing import List, Optional, Tuple
from urllib.parse import urljoin, quote
from urllib.request import Request, urlopen
from pathlib import Path

from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext

# playwright_stealth 1.0.6 API 兼容（无 Stealth 类，用 stealth_sync）
try:
    from playwright_stealth import stealth_sync as _stealth_sync
except ImportError:
    _stealth_sync = None

import config
from store import SqliteTaskStore, get_db_path

# 配置日志
def _setup_logging():
    """配置日志系统，支持详细输出到控制台"""
    log_format = '%(asctime)s [%(levelname)s] %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    
    # 设置日志级别为DEBUG以获取详细信息
    logging.basicConfig(
        level=logging.DEBUG,
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)

logger = _setup_logging()

class BlockedException(Exception):
    """自定义被拦截异常"""
    pass


def _crawl_status_path() -> Path:
    """爬取状态文件路径（与 config 一致，供 backend 读取）"""
    return getattr(config, "CRAWL_STATUS_FILE", Path(__file__).resolve().parent.parent / "data" / "crawl_status.json")


def _notify_backend_register(list_code: str, crawl_type: str) -> None:
    """爬取开始时向服务端注册，状态写入服务端内存，供 /status 读取。"""
    base = getattr(config, "BACKEND_URL", None)
    if not base:
        return
    try:
        url = urljoin(base.rstrip("/") + "/", "api/crawl/register")
        req = Request(
            url,
            data=json.dumps({
                "list_code": (list_code or "").strip().upper(),
                "crawl_type": (crawl_type or "extract").strip(),
                "pid": os.getpid(),
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urlopen(req, timeout=5)
    except Exception:
        pass


def _notify_backend_unregister() -> None:
    """爬取结束时向服务端注销。"""
    base = getattr(config, "BACKEND_URL", None)
    if not base:
        return
    try:
        url = urljoin(base.rstrip("/") + "/", "api/crawl/unregister")
        req = Request(url, method="POST")
        urlopen(req, timeout=5)
    except Exception:
        pass


class MagnetScraper:
    """磁力链接爬虫类：支持 TaskStore（DB）或文件存储"""

    def __init__(
        self,
        max_pages: int = None,
        visible: bool = False,
        store: Optional[SqliteTaskStore] = None,
        list_source_id: Optional[int] = None,
        list_path: Optional[str] = None,
        list_params: Optional[str] = None,
        actor_name: Optional[str] = None,
    ):
        logger.info("=" * 60)
        logger.info("初始化 MagnetScraper")
        logger.info(f"运行环境: Python {sys.version}")
        logger.info(f"操作系统: {platform.system()} {platform.release()}")
        logger.info(f"架构: {platform.machine()}")
        
        # 检测是否在Docker容器内运行
        is_docker = os.path.exists('/.dockerenv') or os.path.exists('/proc/1/cgroup')
        logger.info(f"Docker环境检测: {'是' if is_docker else '否'}")
        if is_docker:
            logger.info("检测到Docker环境，将使用无头模式和特殊浏览器配置")
        
        self.max_pages = max_pages if max_pages is not None and max_pages != 0 else config.MAX_PAGES
        self.no_page_limit = max_pages == 0  # 0 表示不限页数，扫到空页为止
        self.visible = visible
        self.page = None  # Playwright Page 对象
        self.browser = None  # Playwright Browser 对象
        self.context = None  # Playwright BrowserContext 对象
        self.playwright = None  # Playwright 实例
        self.store = store
        self.list_source_id = list_source_id
        # DB 模式下的列表配置（由 list_source 提供）
        self.list_path = list_path or config.LIST_PATH
        self.list_params = list_params or config.LIST_PARAMS
        self.actor_name = actor_name  # 演员过滤：当前列表源的演员名
        self.results_count = 0
        
        logger.info(f"配置参数: max_pages={self.max_pages}, visible={visible}, list_path={self.list_path}, list_params={self.list_params}")
        logger.info(f"存储模式: {'数据库' if store is not None else '文件'}")

        if store is not None:
            self.visited_urls = set()
            self.pending_urls = []
            logger.info("使用数据库存储模式")
        else:
            self.visited_urls = self._load_urls(config.VISITED_URLS_FILE)
            self.pending_urls = self._load_urls(config.PENDING_URLS_FILE, as_list=True)
            logger.info(f"使用文件存储模式: 已访问={len(self.visited_urls)}, 待处理={len(self.pending_urls)}")
        logger.info("=" * 60)

    def _list_code(self) -> str:
        """当前列表源代码，用于状态透出"""
        if self.store and self.list_source_id:
            row = self.store.get_list_source_by_id(self.list_source_id)
            if row:
                return (row.get("list_code") or "").upper()
        if self.list_path:
            return (self.list_path.rstrip("/").split("/")[-1] or "").upper()
        return (getattr(config, "LIST_CODE", "") or "").upper()

    def _process_single_url(self, url: str):
        """提取单个 URL 的磁力链接并更新数据库"""
        if not self.page:
            self.init_browser()

        logger.info(f"单任务提取: {url}")
        self._write_crawl_status(phase="extract", list_code="single", crawl_type="extract-single", total=1)

        success, best_magnet, magnets_json, video_code, title, poster_url, thumbnails_json, synopsis, actors, error_message = self.process_detail_page(url)

        if self.store is not None:
            # 更新数据库中匹配该 URL 的任务（使用 store 的 _conn 上下文管理器）
            with self.store._conn() as conn:
                conn.execute(
                    """UPDATE tasks SET status = ?, best_magnet = ?, magnets_json = ?,
                       video_code = COALESCE(?, video_code), title = COALESCE(?, title),
                       poster_url = COALESCE(?, poster_url), thumbnail_urls = COALESCE(?, thumbnail_urls),
                       synopsis = COALESCE(?, synopsis), description = COALESCE(?, description),
                       actors = COALESCE(?, actors), error_message = ?,
                       updated_at = datetime('now')
                       WHERE url = ?""",
                    (
                        "visited" if success else "failed",
                        best_magnet, magnets_json,
                        video_code, title, poster_url, thumbnails_json, synopsis, synopsis, actors,
                        error_message,
                        url,
                    ),
                )
                conn.commit()

        if success:
            logger.info(f"单任务提取成功: {video_code or url}")
        else:
            logger.error(f"单任务提取失败: {error_message or url}")

    def _write_crawl_status(self, **kwargs) -> None:
        """写入当前爬取状态（供后端/前端轮询）"""
        try:
            path = _crawl_status_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {k: v for k, v in kwargs.items() if v is not None}
            data["updated_at"] = time.time()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    def _clear_crawl_status(self) -> None:
        """清除爬取状态文件"""
        try:
            path = _crawl_status_path()
            if path.exists():
                path.unlink()
        except Exception:
            pass

    def _load_urls(self, file_path: Path, as_list: bool = False):
        """从文件加载 URL（仅文件模式）"""
        urls = [] if as_list else set()
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f if line.strip()]
                    urls = lines if as_list else set(lines)
                logger.info(f"从 {file_path.name} 加载了 {len(urls)} 条记录")
            except Exception as e:
                logger.error(f"加载 {file_path.name} 失败: {e}")
                logger.debug(f"错误详情: {traceback.format_exc()}")
        else:
            logger.debug(f"文件不存在，跳过加载: {file_path}")
        return urls

    def _save_pending_urls(self):
        """将待处理队列同步到文件（仅文件模式）"""
        try:
            config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            with open(config.PENDING_URLS_FILE, "w", encoding="utf-8") as f:
                for url in self.pending_urls:
                    f.write(f"{url}\n")
            logger.debug(f"已保存 {len(self.pending_urls)} 个待处理URL到文件")
        except Exception as e:
            logger.error(f"同步待处理队列失败: {e}")
            logger.debug(f"错误详情: {traceback.format_exc()}")

    def _mark_as_visited_file(self, url: str):
        """标记为已访问并写入文件（仅文件模式）"""
        if url in self.pending_urls:
            self.pending_urls.remove(url)
            self._save_pending_urls()
        try:
            config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            with open(config.VISITED_URLS_FILE, "a", encoding="utf-8") as f:
                f.write(f"{url}\n")
            self.visited_urls.add(url)
            logger.debug(f"已标记URL为已访问: {url}")
        except Exception as e:
            logger.error(f"保存已访问记录失败: {e}")
            logger.debug(f"错误详情: {traceback.format_exc()}")

    def init_browser(self):
        """初始化浏览器（使用 Playwright + playwright-stealth）"""
        logger.info("开始初始化浏览器（Playwright + playwright-stealth）...")
        
        # 检测Docker环境
        is_docker = os.path.exists('/.dockerenv') or os.path.exists('/proc/1/cgroup')
        logger.debug(f"Docker环境: {is_docker}")
        
        try:
            # 启动 Playwright
            self.playwright = sync_playwright().start()
            logger.info("Playwright 启动成功")
            
            # 浏览器启动参数
            launch_options = {
                "headless": not self.visible if not is_docker else (False if self.visible else True),
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                ],
            }
            # Docker 环境没有 headless_shell，用完整 chromium（通过 channel 指定）
            if is_docker:
                launch_options["channel"] = "chromium"

            # 配置代理（必须在 launch 时设置，Chromium 只在启动时读代理）
            proxy_config = None
            proxy_url = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("http_proxy") or os.environ.get("https_proxy")
            if proxy_url:
                logger.info(f"检测到代理配置: {proxy_url}")
                # 解析代理URL，提取 server/username/password
                if proxy_url.startswith("http://") or proxy_url.startswith("https://"):
                    from urllib.parse import urlparse
                    parsed = urlparse(proxy_url)
                    proxy_server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
                    proxy_config = {"server": proxy_server}
                    if parsed.username:
                        proxy_config["username"] = parsed.username
                    if parsed.password:
                        proxy_config["password"] = parsed.password
                else:
                    # 无 scheme 的裸地址 host:port
                    proxy_config = {"server": f"http://{proxy_url}"}
                logger.info(f"代理 server: {proxy_config['server']}")
                # 双保险：launch 级 proxy + Chromium 原生 --proxy-server 参数
                launch_options["proxy"] = proxy_config
                launch_options["args"].append(f"--proxy-server={proxy_config['server']}")
            
            # Docker环境特殊配置
            if is_docker:
                logger.info("应用Docker环境特殊配置...")
                launch_options["args"].extend([
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                ])
                logger.debug("已添加Docker必需参数: --no-sandbox, --disable-dev-shm-usage, --disable-gpu")
            
            # 启动浏览器
            logger.info("启动 Chromium 浏览器...")
            self.browser = self.playwright.chromium.launch(**launch_options)
            logger.info("浏览器启动成功")
            
            # 创建浏览器上下文
            temp_dir = config.OUTPUT_DIR / f"browser_profile_{random.randint(1000, 9999)}"
            logger.info(f"浏览器配置文件目录: {temp_dir}")
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            # User-Agent
            user_agents = [
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            ]
            selected_ua = random.choice(user_agents)
            logger.debug(f"设置User-Agent: {selected_ua}")

            context_options = {
                "user_agent": selected_ua,
                "viewport": {"width": 1920, "height": 1080},
                "locale": "en-US",
                "timezone_id": "America/New_York",
            }
            
            self.context = self.browser.new_context(**context_options)

            # 创建页面
            self.page = self.context.new_page()

            # 应用 playwright-stealth 到 page（1.0.6 API: stealth_sync(page)）
            if _stealth_sync:
                _stealth_sync(self.page)
                logger.info("已应用 playwright-stealth 反检测到 page")
            else:
                logger.warning("playwright-stealth 未安装，跳过反检测")
            
            # 验证浏览器是否正常工作
            try:
                logger.debug("验证浏览器功能...")
                test_url = "about:blank"
                self.page.goto(test_url, wait_until="domcontentloaded", timeout=30000)
                logger.debug(f"浏览器测试访问成功: {test_url}")
            except Exception as e:
                logger.warning(f"浏览器功能验证失败: {e}")
            
            logger.info(f"浏览器初始化成功 (配置文件: {temp_dir.name})")
        except Exception as e:
            logger.error(f"浏览器初始化失败: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")
            if self.playwright:
                try:
                    self.playwright.stop()
                except Exception:
                    pass
            raise

    def _close_browser(self):
        """关闭浏览器（Playwright）"""
        logger.info("关闭浏览器...")
        try:
            if self.page:
                self.page.close()
                self.page = None
            if self.context:
                self.context.close()
                self.context = None
            if self.browser:
                self.browser.close()
                self.browser = None
            if self.playwright:
                self.playwright.stop()
                self.playwright = None
            logger.info("浏览器已关闭")
        except Exception as e:
            logger.warning(f"关闭浏览器时出错: {e}")

    def _check_verification_passed(self) -> bool:
        """检查Cloudflare验证是否已经通过"""
        try:
            current_url = self.page.url
            
            # 检查URL是否从challenge URL变成了正常URL
            if "challenge" in current_url:
                # 如果URL中还有challenge，可能还在验证中
                pass
            else:
                # URL中没有challenge，可能已经通过
                logger.debug("URL中无challenge标识，可能已通过验证")
            
            # 检查是否还有验证相关的文本
            has_security_text = False
            try:
                if self.page.locator("text=Security Verification").count() > 0:
                    has_security_text = True
            except Exception:
                pass
            
            if not has_security_text:
                try:
                    if self.page.locator("text=确认您是真人").count() > 0:
                        has_security_text = True
                except Exception:
                    pass
            
            # 检查页面是否有实际内容（说明验证已通过）
            has_content = False
            try:
                # 检查是否有详情页链接（列表页）
                if self.page.locator("a[href^='/v/']").count() > 0:
                    has_content = True
                    logger.debug("检测到详情页链接，验证可能已通过")
            except Exception:
                pass
            
            if not has_content:
                try:
                    # 检查是否有磁力链接（详情页）
                    if self.page.locator("a[href^='magnet:']").count() > 0:
                        has_content = True
                        logger.debug("检测到磁力链接，验证可能已通过")
                except Exception:
                    pass
            
            if not has_content:
                try:
                    # 检查是否有其他内容元素
                    body_text = self.page.locator("body").inner_text()
                    if body_text and len(body_text.strip()) > 100:
                        # 页面有足够的内容，可能已经通过验证
                        has_content = True
                        logger.debug("检测到页面有内容，验证可能已通过")
                except Exception:
                    pass
            
            # 如果URL中没有challenge，且没有验证文本，且有实际内容，则认为已通过
            if "challenge" not in current_url and not has_security_text and has_content:
                logger.info("检测到验证已通过（URL正常、无验证文本、有实际内容）")
                return True
            
            # 如果URL中没有challenge，且没有验证文本，即使内容不多也认为可能已通过（可能是空页面）
            if "challenge" not in current_url and not has_security_text:
                logger.debug("URL正常且无验证文本，可能已通过（但内容较少）")
                return True
            
            return False
        except Exception as e:
            logger.debug(f"检查验证状态时出错: {e}")
            return False
    
    def _handle_security_check(self):
        """Cloudflare 与年龄验证处理（使用 Playwright）"""
        current_url = self.page.url if self.page else "未知"
        logger.debug(f"开始安全检查，当前URL: {current_url}")
        
        try:
            logger.debug("等待页面加载 (3-5秒)...")
            time.sleep(random.uniform(3, 5))
            
            # 检查当前页面URL
            current_url = self.page.url
            logger.debug(f"页面加载后URL: {current_url}")
            
            # 检查页面标题和内容
            try:
                page_title = self.page.title()
                logger.debug(f"页面标题: {page_title}")
            except Exception as e:
                logger.debug(f"无法获取页面标题: {e}")
            
            # 先检查验证是否已经通过（避免误判）
            if self._check_verification_passed():
                logger.info("验证已通过，跳过验证处理")
                # 处理年龄验证后返回
                try:
                    logger.debug("检查年龄验证...")
                    age_btn = self.page.locator("text=是,我已滿18歲").first
                    if age_btn.count() > 0:
                        logger.info("找到年龄验证按钮，点击确认...")
                        age_btn.click()
                        time.sleep(1)
                        logger.info("年龄验证已通过")
                except Exception as e:
                    logger.debug(f"年龄验证检查异常（可能不存在）: {e}")
                return
            
            # 检查是否有Cloudflare验证
            has_challenge = "challenge" in current_url
            security_elem = None
            confirm_elem = None
            try:
                security_elem = self.page.locator("text=Security Verification").first
                if security_elem.count() == 0:
                    security_elem = None
            except Exception:
                pass
            
            try:
                confirm_elem = self.page.locator("text=确认您是真人").first
                if confirm_elem.count() == 0:
                    confirm_elem = None
            except Exception:
                pass
            
            logger.debug(f"安全检查结果: challenge_in_url={has_challenge}, security_elem={security_elem is not None}, confirm_elem={confirm_elem is not None}")
            
            if has_challenge or security_elem or confirm_elem:
                logger.info("检测到 Cloudflare 验证，开始处理...")
                initial_url = current_url
                consecutive_no_element = 0
                
                for i in range(15):
                    logger.debug(f"尝试处理验证 (第 {i+1}/15 次)...")
                    
                    # 每次循环都检查URL是否变化（说明可能已经通过验证）
                    current_url = self.page.url
                    if "challenge" not in current_url and "challenge" in initial_url:
                        logger.info("检测到URL从challenge URL变为正常URL，验证可能已通过")
                        if self._check_verification_passed():
                            logger.info("验证已通过")
                            return True
                    
                    # 首先尝试在页面中直接查找验证元素
                    btn = None
                    try:
                        # 尝试多种常见的验证按钮选择器
                        selectors = [
                            ".ctp-checkbox-label",
                            "#challenge-stage",
                            "input[type='checkbox']",
                            "label[for*='challenge']",
                            ".ctp-checkbox",
                            "[data-ray]",
                        ]
                        for selector in selectors:
                            try:
                                elem = self.page.locator(selector).first
                                if elem.count() > 0 and elem.is_visible():
                                    btn = elem
                                    logger.debug(f"在页面中找到验证元素: {selector}")
                                    break
                            except Exception:
                                continue
                        
                        # 如果没找到，尝试通过文本查找
                        if not btn:
                            try:
                                btn = self.page.locator("text=确认您是真人").first
                                if btn.count() == 0:
                                    btn = None
                            except Exception:
                                pass
                            if not btn:
                                try:
                                    btn = self.page.locator("text=Verify").first
                                    if btn.count() == 0:
                                        btn = None
                                except Exception:
                                    pass
                    except Exception as e:
                        logger.debug(f"查找页面验证元素时出错: {e}")
                    
                    # 如果页面中没找到，尝试查找 iframe
                    if not btn:
                        logger.debug("页面中未找到验证元素，尝试查找 iframe...")
                        try:
                            iframes = self.page.locator("iframe").all()
                            target_iframe = None
                            for iframe in iframes:
                                try:
                                    src = iframe.get_attribute("src") or ""
                                    title = iframe.get_attribute("title") or ""
                                    if "challenge-platform" in src or "Cloudflare security challenge" in title:
                                        target_iframe = iframe
                                        break
                                except Exception:
                                    continue
                            
                            if target_iframe:
                                logger.info(f"找到验证iframe，切换到 iframe 查找验证按钮...")
                                try:
                                    # 切换到 iframe
                                    frame = target_iframe.content_frame()
                                    if frame:
                                        # 在 iframe 中查找验证按钮
                                        try:
                                            btn = frame.locator(".ctp-checkbox-label").first
                                            if btn.count() == 0:
                                                btn = None
                                        except Exception:
                                            pass
                                        if not btn:
                                            try:
                                                btn = frame.locator("#challenge-stage").first
                                                if btn.count() == 0:
                                                    btn = None
                                            except Exception:
                                                pass
                                        if not btn:
                                            try:
                                                btn = frame.locator("text=确认您是真人").first
                                                if btn.count() == 0:
                                                    btn = None
                                            except Exception:
                                                pass
                                except Exception as e:
                                    logger.debug(f"处理iframe时出错: {e}")
                        except Exception as e:
                            logger.debug(f"查找iframe时出错: {e}")
                    
                    # 如果找到了验证按钮，执行点击
                    if btn:
                        consecutive_no_element = 0  # 重置计数器
                        try:
                            logger.info("找到验证按钮，执行点击...")
                            btn.scroll_into_view_if_needed()
                            time.sleep(0.5)
                            btn.click(timeout=5000)
                            logger.debug("等待验证完成 (5-8秒)...")
                            time.sleep(random.uniform(5, 8))
                            
                            # 验证是否通过
                            if self._check_verification_passed():
                                logger.info("Cloudflare验证已通过")
                                return True
                            else:
                                logger.debug(f"验证仍在进行中 (第 {i+1}/15 次)...")
                        except Exception as e:
                            logger.debug(f"点击验证按钮时出错: {e}")
                    else:
                        consecutive_no_element += 1
                        logger.debug(f"未找到验证元素或iframe (第 {i+1}/15 次，连续 {consecutive_no_element} 次未找到)...")
                        
                        # 如果连续3次找不到验证元素，检查是否已经通过
                        if consecutive_no_element >= 3:
                            logger.debug("连续多次未找到验证元素，检查验证是否已自动通过...")
                            if self._check_verification_passed():
                                logger.info("Cloudflare验证已自动通过")
                                return True
                        
                        # 等待页面自动处理验证
                        logger.debug("等待页面自动处理验证...")
                        time.sleep(3)
                        
                        # 再次检查是否已经通过
                        if self._check_verification_passed():
                            logger.info("Cloudflare验证已自动通过")
                            return True
                    
                    logger.debug(f"等待验证加载 (2秒)...")
                    time.sleep(2)
                
                # 如果循环结束，最后再检查一次
                if self._check_verification_passed():
                    logger.info("循环结束后检测到验证已通过")
                    return True
                
                logger.error("Cloudflare 验证超时，无法通过")
                raise BlockedException("Cloudflare 拦截无法通过")
            else:
                logger.debug("未检测到Cloudflare验证")
        except BlockedException:
            logger.error("被Cloudflare拦截")
            raise
        except Exception as e:
            logger.warning(f"安全检查处理异常: {e}")
            logger.debug(f"异常详情: {traceback.format_exc()}")
        
        # 处理年龄验证
        try:
            logger.debug("检查年龄验证...")
            age_btn = self.page.locator("text=是,我已滿18歲").first
            if age_btn.count() > 0:
                logger.info("找到年龄验证按钮，点击确认...")
                age_btn.click()
                time.sleep(1)
                logger.info("年龄验证已通过")
            else:
                logger.debug("未找到年龄验证")
        except Exception as e:
            logger.debug(f"年龄验证检查异常（可能不存在）: {e}")

    def get_list_url(self, page_num: int) -> str:
        """生成列表页 URL"""
        base = f"{config.BASE_URL}{self.list_path}"
        if page_num == 1:
            return f"{base}?{self.list_params}"
        return f"{base}?{self.list_params}&page={page_num}"

    def append_magnet_to_file(self, magnet: str):
        """将磁力链接追加到 TXT 文件（仅文件模式）"""
        try:
            config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            with open(config.MAGNETS_LIST_FILE, "a", encoding="utf-8") as f:
                f.write(f"{magnet}\n")
            self.results_count += 1
            logger.info(f"发现磁力并保存 (总计: {self.results_count})")
        except Exception as e:
            logger.error(f"保存磁力失败: {e}")
            logger.debug(f"错误详情: {traceback.format_exc()}")

    def process_detail_page(self, url: str) -> Tuple[bool, Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        """处理详情页并提取磁力。返回 (success, best_magnet, magnets_json, video_code, title, poster_url, thumbnails_json, synopsis, actors, error_message)。"""
        logger.info(f"开始处理详情页: {url}")
        try:
            logger.debug(f"访问URL: {url}")
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            logger.debug(f"页面访问完成，当前URL: {self.page.url}")

            try:
                page_title = self.page.title()
                logger.debug(f"页面标题: {page_title}")
            except Exception as e:
                logger.warning(f"无法获取页面标题: {e}")

            logger.debug("执行安全检查...")
            self._handle_security_check()

            delay = random.uniform(2, 4)
            logger.debug(f"等待 {delay:.2f} 秒后开始查找磁力链接...")
            time.sleep(delay)

            # --- 提取视频元信息（完整版） ---
            title = self._extract_title()
            poster_url = self._extract_poster()
            thumbnails = self._extract_thumbnails()
            synopsis = self._extract_synopsis()
            actors = self._extract_actors()
            video_code = self._extract_video_code_from_page()
            description = self._extract_description()
            tags = self._extract_tags()
            meta = self._extract_meta_panel()

            logger.info(f"元信息 - 标题: {title}, 海报: {'有' if poster_url else '无'}, 缩略图: {len(thumbnails)}张, "
                        f"简介: {'有' if synopsis else '无'}, 演员: {'有' if actors else '无'}, "
                        f"标签: {'有' if tags else '无'}, 元数据: {len(meta)}项")

            # --- 演员验证（在提取磁力前）---
            # 只有当能提取到演员列表但不匹配时才跳过；无法提取演员时不阻断（可能在页面其他地方）
            if self.actor_name and actors:
                actor_lower = self.actor_name.lower()
                if not any(actor_lower in a.lower() for a in actors.split(",")):
                    logger.warning(f"演员不匹配: {actors} 中不包含 {self.actor_name}")
                    return False, None, None, video_code, title, poster_url, json.dumps(thumbnails) if thumbnails else None, synopsis, actors, "演员不匹配"
            # elif self.actor_name and not actors:
            #     logger.warning("无法提取演员信息，跳过")
            #     return False, None, None, video_code, title, poster_url, json.dumps(thumbnails) if thumbnails else None, synopsis, actors, "无法提取演员信息"

            # --- 提取磁力链接 ---
            logger.debug("查找磁力链接元素...")
            magnet_links = self.page.locator("a[href^='magnet:']").all()
            logger.info(f"找到 {len(magnet_links)} 个磁力链接")

            if not magnet_links:
                logger.warning("未找到磁力链接")
                try:
                    page_html = self.page.content()[:500]
                    logger.debug(f"页面HTML片段（前500字符）: {page_html}")
                except Exception as e:
                    logger.debug(f"无法获取页面HTML: {e}")
                return False, None, None, video_code, title, poster_url, json.dumps(thumbnails) if thumbnails else None, synopsis, actors, "未找到磁力链接"

            magnets_info = []
            logger.debug("提取磁力链接信息...")
            for i, link in enumerate(magnet_links):
                try:
                    href = link.get_attribute("href")
                    name = ""
                    try:
                        name_elem = link.locator(".name").first
                        if name_elem.count() > 0:
                            name = name_elem.inner_text() or ""
                    except Exception:
                        pass
                    magnets_info.append({"magnet": href, "name": name})
                    logger.debug(f"磁力链接 {i+1}: {href[:50]}... (名称: {name})")
                except Exception as e:
                    logger.warning(f"提取第 {i+1} 个磁力链接信息失败: {e}")

            # 磁力去重
            if self.store and hasattr(self.store, 'is_magnet_duplicate'):
                from store import _extract_magnet_hash
                seen_hashes = set()
                deduped = []
                for m in magnets_info:
                    h = _extract_magnet_hash(m["magnet"])
                    if h and h in seen_hashes:
                        logger.debug(f"去重: 跳过重复磁力 {h[:16]}...")
                        continue
                    if h and self.store.is_magnet_duplicate(m["magnet"]):
                        logger.debug(f"去重: 数据库已存在磁力 {h[:16]}...")
                        continue
                    if h:
                        seen_hashes.add(h)
                    deduped.append(m)
                if deduped:
                    magnets_info = deduped
                    logger.info(f"去重后剩余 {len(magnets_info)} 个磁力链接")

            logger.debug("按优先级排序磁力链接...")
            # 从 DB 读 preferred_suffixes（覆盖 config 硬编码值）
            suffixes = config.PREFERRED_SUFFIXES
            if self.store:
                try:
                    with self.store._conn() as conn:
                        row = conn.execute(
                            "SELECT value FROM settings WHERE key='preferred_suffixes' LIMIT 1"
                        ).fetchone()
                        if row and row[0]:
                            db_suffixes = [s.strip() for s in row[0].split(",") if s.strip()]
                            if db_suffixes:
                                suffixes = db_suffixes
                except Exception:
                    pass
            def get_priority(m):
                n = (m["name"] or "").upper()
                for i, suffix in enumerate(suffixes):
                    if suffix.upper() in n:
                        return i
                return len(suffixes)
            magnets_info.sort(key=get_priority)

            best_magnet = magnets_info[0]["magnet"]
            magnets_json = json.dumps([{"magnet": x["magnet"], "name": x["name"]} for x in magnets_info], ensure_ascii=False)

            # 保存额外元数据到实例属性，供 extract_magnets 使用
            self._last_extra_meta = {
                "description": description,
                "tags": tags,
                "release_date": meta.get("release_date"),
                "duration": meta.get("duration"),
                "director": meta.get("director"),
                "maker": meta.get("maker"),
                "label": meta.get("label"),
                "series": meta.get("series"),
                "rating": meta.get("rating"),
                "file_size": meta.get("file_size"),
            }

            logger.info(f"成功提取磁力链接，最佳链接: {best_magnet[:50]}...")
            return True, best_magnet, magnets_json, video_code, title, poster_url, json.dumps(thumbnails) if thumbnails else None, synopsis, actors, None
        except BlockedException:
            logger.error("处理详情页时被拦截")
            raise
        except Exception as e:
            error_msg = str(e)
            logger.error(f"处理详情页失败: {error_msg}")
            logger.error(f"错误详情: {traceback.format_exc()}")
            return False, None, None, None, None, None, None, None, None, error_msg

    def _extract_title(self) -> Optional[str]:
        """提取视频标题"""
        try:
            el = self.page.locator(".current-title, h2.title, .video-title").first
            if el.count() > 0:
                return (el.inner_text() or "").strip()
        except Exception:
            pass
        try:
            title = self.page.title()
            if title and "JavDB" not in title:
                return title.strip()
            parts = title.split("-", 1) if title else []
            if len(parts) > 1:
                return parts[1].strip()
        except Exception:
            pass
        return None

    def _extract_poster(self) -> Optional[str]:
        """提取海报大图 URL。

        JavDB 详情页 gallery 结构：
        - #gallery-1: 缩略图（竖图）
        - #gallery-2: 缩略图（竖图）
        - #gallery-3: 正式封面（横图）← 这才是海报
        - .boxcover img: 页面顶部的封面图
        """
        # 方法1: JavDB gallery-3 是正式封面（横版大图）
        try:
            g3 = self.page.locator("#gallery-3 img").first
            if g3.count() > 0:
                src = g3.get_attribute("src") or g3.get_attribute("data-src") or g3.get_attribute("data-original")
                if src and src.startswith("http"):
                    return src
        except Exception:
            pass

        # 方法2: .boxcover / .cover-container（页面顶部封面区域）
        try:
            img = self.page.locator(".boxcover img, .cover-container img, .video-cover img, img.boxcover").first
            if img.count() > 0:
                src = img.get_attribute("src") or img.get_attribute("data-src")
                if src and src.startswith("http"):
                    return src
        except Exception:
            pass

        # 方法3: 从 cover URL 模式匹配
        try:
            imgs = self.page.locator("img[src*='jdbstatic']").all()
            for img in imgs:
                src = img.get_attribute("src") or img.get_attribute("data-src")
                if src and "cover" in src.lower() and src.startswith("http"):
                    return src
        except Exception:
            pass

        # 方法4: fallback 到 gallery-1
        try:
            g1 = self.page.locator("#gallery-1 img").first
            if g1.count() > 0:
                src = g1.get_attribute("src") or g1.get_attribute("data-src")
                if src and src.startswith("http"):
                    return src
        except Exception:
            pass

        return None

    def _extract_thumbnails(self) -> list:
        """提取预览图 URL 列表（高清原图优先，不含封面）。

        JavDB 详情页结构：
        - /covers/ 路径: 横版封面大图（由 _extract_poster 处理，不属于缩略图）
        - .preview-images 容器内 a.tile-item 的 href = 高清原图（点击才打开的大图）
        - a.tile-item 内部 img.src = 小缩略图（147x200 / 120x90，仅预览用）
        - /samples/ 路径: 竖版预览图缩略图

        优先级：a.tile-item href（高清原图）→ img src（小缩略图兜底）
        """
        thumbnails = []
        seen = set()

        def _add_url(src):
            if not src or not src.startswith("http"):
                return False
            if src in seen:
                return False
            # 排除封面图（/covers/ 路径），只保留预览图
            if "/covers/" in src:
                return False
            seen.add(src)
            thumbnails.append(src)
            return True

        def _abs(href):
            """相对 URL → 绝对 URL"""
            if not href:
                return None
            if href.startswith("http"):
                return href
            if href.startswith("//"):
                return "https:" + href
            if href.startswith("/"):
                base = self.base_url.rstrip("/")
                return base + href
            return None

        # 方法1（首选）：.preview-images a.tile-item 的 href = 高清原图
        try:
            tiles = self.page.locator(".preview-images a.tile-item, .preview-images a[href]").all()
            for t in tiles:
                try:
                    href = t.get_attribute("href")
                    url = _abs(href)
                    if url:
                        _add_url(url)
                except Exception:
                    pass
        except Exception:
            pass

        # 方法2: gallery 图片（排除 #gallery-3 封面）—— 取 img src（小缩略图兜底）
        if not thumbnails:
            try:
                for i in range(1, 30):
                    if i == 3:
                        continue  # gallery-3 是封面，不属于缩略图
                    g = self.page.locator(f"#gallery-{i} img").first
                    if g.count() > 0:
                        src = g.get_attribute("src") or g.get_attribute("data-src")
                        _add_url(src)
                    else:
                        break
            except Exception:
                pass

        # 方法3: 预览图区域 img（小缩略图兜底）
        if not thumbnails:
            try:
                imgs = self.page.locator(".preview-images img, .sample-box img, .video-review img, img.preview, .tile-item img").all()
                for img in imgs:
                    try:
                        src = img.get_attribute("src") or img.get_attribute("data-src") or img.get_attribute("data-original")
                        _add_url(src)
                    except Exception:
                        pass
            except Exception:
                pass

        # 方法4: fallback — 按 /samples/ URL 模式匹配
        if not thumbnails:
            try:
                imgs = self.page.locator("img[src*='/samples/']").all()
                for img in imgs:
                    try:
                        src = img.get_attribute("src")
                        _add_url(src)
                    except Exception:
                        pass
            except Exception:
                pass

        return thumbnails

    def _extract_synopsis(self) -> Optional[str]:
        """提取视频简介"""
        try:
            el = self.page.locator(".synopsis, .description, .video-desc, [itemprop='description']").first
            if el.count() > 0:
                text = (el.inner_text() or "").strip()
                if text:
                    return text[:2000]
        except Exception:
            pass
        return None

    def _extract_actors(self) -> Optional[str]:
        """提取演员列表，返回逗号分隔的演员名"""
        actors = []
        seen = set()
        try:
            # 方法1: 直接查找所有包含 /actors/ 的链接（最可靠）
            try:
                all_actor_links = self.page.locator("a[href*='/actors/']").all()
                for link in all_actor_links:
                    try:
                        name = (link.inner_text() or "").strip()
                        if name and len(name) > 1 and name.lower() not in seen:
                            # 过滤无关关键词
                            if any(kw in name.lower() for kw in ['评论', '喜欢', '收藏', '下载', '分享', 'tags', 'category', 'search']):
                                continue
                            seen.add(name.lower())
                            actors.append(name)
                    except Exception:
                        pass
                if len(actors) >= 3:
                    return ",".join(actors[:20])
            except Exception:
                pass

            # 方法2: 通过特定选择器（更精确但可能遗漏）
            selectors = [
                # JavDB 主要演员区域
                ".tile-tags a[href*='/actors/']",
                # 影片信息面板
                ".movie-panel-info a[href*='/actors/']",
                # 右侧信息栏
                ".panel-section a[href*='/actors/']",
                # 元数据类型 D 的元素
                "[data-type='D'] a[href*='/actors/']",
                # 视频元信息
                ".video-meta-info a[href*='/actors/']",
                # 演员名字
                ".star-name a[href*='/actors/']",
                # 详细区域
                ".detail-section a[href*='/actors/']",
                # 预览瓦片
                ".preview-tiles a[href*='/actors/']",
                # 通用面板链接
                ".panel a[href*='/actors/']",
                # 带有 title 属性的演员链接
                "a[href*='/actors/'][title]",
            ]
            for sel in selectors:
                try:
                    links = self.page.locator(sel).all()
                    if links:
                        for link in links:
                            try:
                                name = (link.inner_text() or "").strip()
                                if name and len(name) > 1 and name.lower() not in seen:
                                    if any(kw in name.lower() for kw in ['评论', '喜欢', '收藏', '下载', '分享', 'tags', 'category', 'search']):
                                        continue
                                    seen.add(name.lower())
                                    actors.append(name)
                            except Exception:
                                pass
                        if len(actors) >= 3:
                            break
                except Exception:
                    pass
            if actors:
                return ",".join(actors[:20])
        except Exception:
            pass
        return None

    def _extract_video_code_from_page(self) -> Optional[str]:
        """从详情页提取番号。

        JavDB 详情页番号位置（按可靠性排序）：
        1. .panel-block:first .value — 元数据面板第一行（番号）
        2. 磁力链 name — [javdb.com]MVSD-696
        3. 页面标题 — MVSD-696 xxxxx | JavDB
        注意：URL 的 /v/xxx 是 JavDB 内部 ID，不是番号！
        """
        import re

        # 方法1: 元数据面板（JavDB 用 Bulma，番号在 .panel-block 第一行的 .value）
        for sel in [".panel-block .value", ".movie-panel-info .value",
                    ".first-block .value", ".video-meta-panel .value",
                    "[itemprop='name']", ".video-code"]:
            try:
                el = self.page.locator(sel).first
                if el.count() > 0:
                    text = (el.inner_text() or "").strip()
                    if text and re.match(r'^[A-Za-z]{2,6}[-_]?\d{2,5}', text):
                        return text
            except Exception:
                pass

        # 方法2: 从磁力链 name 提取（[javdb.com]MVSD-696 → MVSD-696）
        try:
            magnets = self.page.locator("a[href^='magnet:'] .name").all()
            for m in magnets[:3]:
                name = (m.inner_text() or "").strip()
                # 匹配番号格式：字母-数字（如 MVSD-696, EBWH-341）
                match = re.search(r'([A-Za-z]{2,6}[-_]\d{2,5})', name)
                if match:
                    return match.group(1)
        except Exception:
            pass

        # 方法3: 从页面标题提取（MVSD-696 xxxxx | JavDB）
        try:
            title = self.page.title() or ""
            match = re.search(r'([A-Za-z]{2,6}[-_]\d{2,5})', title)
            if match:
                return match.group(1)
        except Exception:
            pass

        return None
        return None

    def _extract_description(self) -> Optional[str]:
        """提取影片简介/描述"""
        try:
            el = self.page.locator(".synopsis, .description, .video-desc, [itemprop='description'], .detail-content").first
            if el.count() > 0:
                return (el.inner_text() or "").strip()[:2000]
        except Exception:
            pass
        return None

    def _extract_tags(self) -> Optional[str]:
        """提取标签列表，返回逗号分隔"""
        tags = []
        try:
            # 从标签区域提取
            tag_els = self.page.locator(".tags a, .tag-list a, .categories a, .genre a, .tile-tags a[href*='/tags/']").all()
            if not tag_els:
                tag_els = self.page.locator("a[href*='/tags/']").all()
            for el in tag_els:
                try:
                    t = (el.inner_text() or "").strip()
                    if t and len(t) > 0:
                        tags.append(t)
                except Exception:
                    pass
        except Exception:
            pass
        return ",".join(tags[:30]) if tags else None

    def _extract_meta_panel(self) -> dict:
        """从详情页元数据面板提取结构化信息（发行日期/时长/导演/制作商/系列/评分等）"""
        meta = {}
        try:
            # 尝试通用的元数据行选择器
            rows = self.page.locator(".movie-panel-info .row, .meta-row, .info-panel .item, .video-meta-panel .row").all()
            for row in rows:
                try:
                    # 尝试 label: value 结构
                    label_el = row.locator(".label, .key, .meta-label, .meta-key, dt").first
                    value_el = row.locator(".value, .meta-value, dd").first
                    label = (label_el.inner_text() or "").strip() if label_el.count() > 0 else ""
                    value = (value_el.inner_text() or "").strip() if value_el.count() > 0 else ""

                    if not label or not value:
                        # 尝试从单个文本节点解析
                        full_text = (row.inner_text() or "").strip()
                        if ":" in full_text:
                            parts = full_text.split(":", 1)
                            label = parts[0].strip()
                            value = parts[1].strip()
                        elif "：" in full_text:
                            parts = full_text.split("：", 1)
                            label = parts[0].strip()
                            value = parts[1].strip()
                        else:
                            continue

                    # 映射到标准字段
                    label_lower = label.lower()
                    if any(kw in label_lower for kw in ['發行日期', '发行日期', 'release', 'date', '日期']):
                        meta["release_date"] = value
                    elif any(kw in label_lower for kw in ['時長', '时长', 'duration', '長度', '长度']):
                        meta["duration"] = value
                    elif any(kw in label_lower for kw in ['導演', '导演', 'director']):
                        meta["director"] = value
                    elif any(kw in label_lower for kw in ['製作商', '制作商', 'maker', 'manufacturer']):
                        meta["maker"] = value
                    elif any(kw in label_lower for kw in ['發行商', '发行商', 'publisher', 'label']):
                        meta["label"] = value
                    elif any(kw in label_lower for kw in ['系列', 'series']):
                        meta["series"] = value
                    elif any(kw in label_lower for kw in ['評分', '评分', 'score', 'rating']):
                        meta["rating"] = value
                    elif any(kw in label_lower for kw in ['大小', 'size', '文件']):
                        meta["file_size"] = value
                except Exception:
                    continue
        except Exception:
            pass
        return meta

    def scan_list_pages(self, keep_browser_open: bool = False, update_mode: bool = False):
        """扫描列表页并填充待处理队列。update_mode=True 时从第 1 页开始，遇本页无新任务即停止（更新）；否则扫满 max_pages（全量扫描）。"""
        logger.info("=" * 60)
        logger.info("开始扫描列表页")
        if update_mode:
            logger.info("扫描模式: 更新（从第 1 页起扫，遇本页无新任务即停止）")
        else:
            logger.info("扫描模式: 全量（将扫描全部指定页数）")
        logger.info(f"扫描范围: 第 1～{'不限' if self.no_page_limit else self.max_pages} 页")
        
        list_code = self._list_code()
        logger.info(f"列表代码: {list_code}")
        logger.info(f"列表路径: {self.list_path}")
        logger.info(f"列表参数: {self.list_params}")
        
        self._write_crawl_status(phase="scan", list_code=list_code, crawl_type="scan", page_current=1, page_max=0 if self.no_page_limit else self.max_pages)

        if not self.page:
            logger.info("浏览器未初始化，开始初始化...")
            self.init_browser()
        else:
            logger.info("使用现有浏览器实例")
        
        new_total = 0
        page_num = 1
        try:
            while self.no_page_limit or page_num <= self.max_pages:
                logger.info("-" * 60)
                logger.info(f"扫描第 {page_num}{'/' + str(self.max_pages) if not self.no_page_limit else ''} 页")

                self._write_crawl_status(phase="scan", list_code=list_code, crawl_type="scan", page_current=page_num, page_max=0 if self.no_page_limit else self.max_pages)
                list_url = self.get_list_url(page_num)
                logger.info(f"列表页URL: {list_url}")
                
                try:
                    logger.debug(f"访问列表页...")
                    self.page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
                    logger.debug(f"页面访问完成，当前URL: {self.page.url}")
                    
                    # 检查页面标题
                    try:
                        page_title = self.page.title()
                        logger.debug(f"页面标题: {page_title}")
                    except Exception as e:
                        logger.debug(f"无法获取页面标题: {e}")
                    
                    logger.debug("执行安全检查...")
                    self._handle_security_check()
                    
                    logger.debug("等待3秒后查找详情链接...")
                    time.sleep(3)
                    
                    logger.debug("查找详情页链接 (a[href^='/v/'])...")
                    links = self.page.locator("a[href^='/v/']").all()
                    logger.info(f"找到 {len(links)} 个详情页链接")
                    
                    if not links:
                        logger.warning(f"第 {page_num} 页无详情链接，停止扫描")
                        # 尝试获取页面内容用于调试
                        try:
                            page_html = self.page.content()[:500]
                            logger.debug(f"页面HTML片段（前500字符）: {page_html}")
                        except Exception as e:
                            logger.debug(f"无法获取页面HTML: {e}")
                        break
                
                    if self.store is not None:
                        logger.debug("使用数据库存储模式处理链接...")
                        visited = self.store.get_visited_urls(self.list_source_id)
                        logger.debug(f"已访问URL数量: {len(visited)}")
                        to_add = []
                        for link in links:
                            try:
                                href = link.get_attribute("href")
                                if href:
                                    full_url = urljoin(config.BASE_URL, href)
                                    if full_url not in visited:
                                        to_add.append(full_url)
                            except Exception as e:
                                logger.warning(f"提取链接失败: {e}")
                        
                        logger.debug(f"准备添加 {len(to_add)} 个新URL到数据库...")
                        added = self.store.add_pending_urls(self.list_source_id, to_add)
                        new_total += added
                        logger.info(f"本页发现 {added} 个新任务（共 {len(links)} 条链接，已存在 {len(links) - added} 条）")
                        
                        if update_mode and len(links) > 0 and added == 0:
                            logger.info("本页链接均已存在，更新完成")
                            break
                    else:
                        logger.debug("使用文件存储模式处理链接...")
                        new_found = 0
                        for link in links:
                            try:
                                href = link.get_attribute("href")
                                if href:
                                    full_url = urljoin(config.BASE_URL, href)
                                    if full_url not in self.visited_urls and full_url not in self.pending_urls:
                                        self.pending_urls.append(full_url)
                                        new_found += 1
                                        new_total += 1
                            except Exception as e:
                                logger.warning(f"提取链接失败: {e}")
                        
                        if new_found > 0:
                            logger.debug("保存待处理URL到文件...")
                            self._save_pending_urls()
                            logger.info(f"本页发现 {new_found} 个新任务（共 {len(links)} 条链接）")
                        else:
                            logger.debug(f"本页无新任务（共 {len(links)} 条链接，均已存在）")
                        
                        if update_mode and len(links) > 0 and new_found == 0:
                            logger.info("本页链接均已存在，更新完成")
                            break
                except Exception as e:
                    logger.error(f"访问第 {page_num} 页时出错: {e}")
                    logger.error(f"错误详情: {traceback.format_exc()}")
                    break
                
                page_num += 1
                delay = random.uniform(2, 4)
                logger.debug(f"等待 {delay:.2f} 秒后继续下一页...")
                time.sleep(delay)
        except BlockedException:
            logger.error("扫描过程中被拦截")
            raise
        except Exception as e:
            logger.error(f"扫描过程出错: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")
            raise
        finally:
            logger.info("清理扫描状态...")
            self._clear_crawl_status()
            if not keep_browser_open:
                self._close_browser()
        
        logger.info("=" * 60)
        logger.info(f"扫描完成! 本次共新增 {new_total} 个任务")
        logger.info("=" * 60)

    def _resolve_pending_url(self, pending_url: str) -> Optional[str]:
        """将 pending://list_code/video_code 解析为真实详情页 URL（通过站内搜索）。"""
        logger.debug(f"解析pending URL: {pending_url}")
        
        if not pending_url.startswith("pending://"):
            logger.warning(f"URL格式不正确，不是pending://开头: {pending_url}")
            return None
        
        try:
            part = pending_url.replace("pending://", "", 1).strip()
            if "/" not in part:
                logger.warning(f"URL格式不正确，缺少分隔符: {pending_url}")
                return None
            list_code, video_code = part.split("/", 1)
            video_code = video_code.strip()
            if not video_code:
                logger.warning(f"番号为空: {pending_url}")
                return None
            logger.debug(f"解析结果: list_code={list_code}, video_code={video_code}")
        except Exception as e:
            logger.error(f"解析pending URL格式失败: {e}")
            logger.debug(f"异常详情: {traceback.format_exc()}")
            return None
        
        search_url = f"{config.BASE_URL}/search?q={quote(video_code)}&f=download"
        logger.info(f"解析番号 {video_code} -> 搜索URL: {search_url}")
        self._write_crawl_status(phase="resolve", list_code=list_code, crawl_type="extract", current_video_code=video_code, message="解析番号中")
        
        try:
            logger.debug(f"访问搜索页面...")
            self.page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            logger.debug(f"搜索页面访问完成，当前URL: {self.page.url}")
            
            logger.debug("执行安全检查...")
            self._handle_security_check()
            
            delay = random.uniform(2, 4)
            logger.debug(f"等待 {delay:.2f} 秒后查找搜索结果...")
            time.sleep(delay)
            
            logger.debug("查找搜索结果中的详情页链接...")
            links = self.page.locator("a[href^='/v/']").all()
            logger.info(f"搜索找到 {len(links)} 个详情页链接")
            
            if not links:
                logger.warning(f"搜索未找到详情页: {video_code}")
                # 尝试获取页面内容用于调试
                try:
                    page_html = self.page.content()[:500]
                    logger.debug(f"搜索页面HTML片段（前500字符）: {page_html}")
                except Exception as e:
                    logger.debug(f"无法获取页面HTML: {e}")
                return None
            
            href = links[0].get_attribute("href")
            if href:
                real_url = urljoin(config.BASE_URL, href)
                logger.info(f"解析成功，真实URL: {real_url}")
                return real_url
            else:
                logger.warning("第一个链接没有href属性")
                return None
        except BlockedException:
            logger.error("解析番号时被拦截")
            raise
        except Exception as e:
            logger.error(f"解析番号失败: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")
            return None

    def extract_magnets(
        self,
        keep_browser_open: bool = False,
        limit: Optional[int] = None,
        failed_only: bool = False,
    ):
        """从待处理队列或失败队列提取磁力（DB 或文件）。支持 pending:// 番号任务，先解析为真实 URL 再爬。"""
        logger.info("=" * 60)
        logger.info("开始提取磁力链接")
        logger.info(f"模式: {'仅重试失败任务' if failed_only else '提取待处理任务'}")
        logger.info(f"限制数量: {limit if limit else '无限制'}")
        
        if self.store is not None:
            logger.debug("从数据库获取任务列表...")
            if failed_only:
                pending = self.store.get_failed_urls(self.list_source_id, limit=limit)
                logger.info(f"从数据库获取失败任务: {len(pending)} 个")
            else:
                pending = self.store.get_pending_urls(self.list_source_id, limit=limit)
                logger.info(f"从数据库获取待处理任务: {len(pending)} 个")
        else:
            logger.debug("从文件获取任务列表...")
            pending = list(self.pending_urls)
            logger.info(f"从文件获取待处理任务: {len(pending)} 个")
        
        if not pending:
            if failed_only:
                logger.warning("失败队列为空，无需重试")
            else:
                logger.warning("待处理队列为空，请先运行 scan 或按番号创建任务")
            return
        
        total_tasks = len(pending)
        logger.info(f"开始提取磁力，共 {total_tasks} 个任务" + ("（仅重试失败）" if failed_only else ""))
        
        list_code = self._list_code()
        crawl_type = "extract_failed" if failed_only else "extract"
        self._write_crawl_status(phase="extract", list_code=list_code, crawl_type=crawl_type, total=total_tasks)
        
        if not self.page:
            logger.info("浏览器未初始化，开始初始化...")
            self.init_browser()
        else:
            logger.info("使用现有浏览器实例")
        try:
            for i, url in enumerate(pending):
                logger.info("-" * 60)
                logger.info(f"任务进度: {i+1}/{total_tasks}")
                logger.info(f"处理URL: {url}")
                
                if self.store is not None:
                    visited = self.store.get_visited_urls(self.list_source_id)
                    if url in visited:
                        logger.debug(f"URL已访问过，跳过: {url}")
                        continue
                    
                    if url.startswith("pending://"):
                        logger.info(f"检测到pending URL，需要解析番号: {url}")
                        try:
                            _part = url.replace("pending://", "", 1).strip()
                            _vc = _part.split("/", 1)[1].strip() if "/" in _part else ""
                            logger.debug(f"提取的番号: {_vc}")
                        except Exception as e:
                            logger.warning(f"解析pending URL失败: {e}")
                            _vc = ""
                        
                        self._write_crawl_status(phase="extract", list_code=list_code, crawl_type=crawl_type, current_index=i + 1, total=total_tasks, current_video_code=_vc or None)
                        
                        logger.info("开始解析番号为真实URL...")
                        real_url = self._resolve_pending_url(url)
                        if not real_url:
                            logger.error("番号解析失败")
                            self.store.mark_failed(url, "番号解析失败")
                            continue
                        
                        logger.info(f"解析成功，真实URL: {real_url}")
                        
                        if self.store.task_exists_with_url(real_url):
                            logger.debug("真实URL对应的任务已存在，删除pending任务")
                            self.store.delete_task_by_url(url)
                            continue
                        
                        if not self.store.update_task_url(url, real_url):
                            logger.warning("更新任务URL失败，删除pending任务")
                            self.store.delete_task_by_url(url)
                            continue
                        
                        url = real_url
                        if url in visited:
                            logger.debug(f"解析后的URL已访问过，跳过: {url}")
                            continue
                else:
                    if url in self.visited_urls:
                        logger.debug(f"URL已访问过，从待处理队列移除: {url}")
                        self.pending_urls.remove(url)
                        self._save_pending_urls()
                        continue
                
                self._write_crawl_status(phase="extract", list_code=list_code, crawl_type=crawl_type, current_index=i + 1, total=total_tasks, current_video_code=None, message="详情页")
                
                delay = random.uniform(config.DETAIL_DELAY_MIN, config.DETAIL_DELAY_MAX)
                logger.debug(f"等待 {delay:.2f} 秒后处理...")
                time.sleep(delay)
                
                success, best_magnet, magnets_json, video_code, title, poster_url, thumbnails_json, synopsis, actors, err_msg = self.process_detail_page(url)

                if video_code:
                    self._write_crawl_status(phase="extract", list_code=list_code, crawl_type=crawl_type, current_index=i + 1, total=total_tasks, current_video_code=video_code, message="已获取磁力" if success else "失败")

                if success and best_magnet:
                    logger.info(f"✓ 成功提取磁力链接: {best_magnet[:50]}...")
                    if self.store is not None:
                        logger.debug("保存到数据库...")
                        extra = getattr(self, "_last_extra_meta", {}) or {}
                        self.store.mark_visited(
                            url,
                            best_magnet=best_magnet,
                            magnets_json=magnets_json,
                            video_code=video_code,
                            title=title,
                            poster_url=poster_url,
                            thumbnail_urls=thumbnails_json,
                            synopsis=synopsis,
                            actors=actors,
                            description=extra.get("description"),
                            tags=extra.get("tags"),
                            release_date=extra.get("release_date"),
                            duration=extra.get("duration"),
                            director=extra.get("director"),
                            maker=extra.get("maker"),
                            label=extra.get("label"),
                            series=extra.get("series"),
                            rating=extra.get("rating"),
                            file_size=extra.get("file_size"),
                        )
                        # 建立演员-作品关联
                        if actors and video_code:
                            try:
                                actor_names = [a.strip() for a in actors.split(",") if a.strip()]
                                for aname in actor_names[:10]:  # 最多关联前10个演员
                                    # 查找或创建演员记录（upsert_actor 返回 actor_id: int）
                                    actor_id = self.store.upsert_actor(aname)
                                    if actor_id:
                                        task_row = self.store.get_task_by_url(url)
                                        if task_row:
                                            self.store.link_actor_movie(actor_id, task_row["id"])
                            except Exception:
                                pass
                        logger.info("已保存到数据库")
                    else:
                        logger.debug("保存到文件...")
                        self.append_magnet_to_file(best_magnet)
                        self._mark_as_visited_file(url)
                        logger.info("已保存到文件")
                else:
                    error_msg = err_msg or "未知错误"
                    logger.warning(f"✗ 处理失败: {error_msg}")
                    if self.store is not None:
                        self.store.mark_failed(url, error_msg)
                    logger.info("任务已标记为失败，稍后可重试")
        except BlockedException:
            logger.error("提取过程中被拦截")
            raise
        except Exception as e:
            logger.error(f"提取过程出错: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")
            raise
        finally:
            logger.info("清理提取状态...")
            self._clear_crawl_status()
            if not keep_browser_open:
                self._close_browser()
        
        logger.info("=" * 60)
        logger.info("队列任务处理完成")
        logger.info("=" * 60)

    def auto_run(self, max_iterations: Optional[int] = None, keep_browser_open: bool = False):
        """自动连续执行：扫描 -> 提取，可循环"""
        logger.info("=" * 60)
        logger.info("开始自动连续执行模式")
        logger.info(f"最大迭代次数: {max_iterations if max_iterations else '无限制'}")
        
        iteration = 0
        max_iter = max_iterations or float("inf")
        try:
            while iteration < max_iter:
                iteration += 1
                logger.info("=" * 60)
                logger.info(f"第 {iteration} 轮")
                logger.info("=" * 60)
                
                try:
                    self.scan_list_pages(keep_browser_open=True)
                except BlockedException:
                    logger.error("扫描时被拦截，停止自动模式")
                    raise
                except Exception as e:
                    logger.error(f"列表页扫描出错: {e}")
                    logger.debug(f"错误详情: {traceback.format_exc()}")
                
                if self.store is not None:
                    pending = self.store.get_pending_urls(self.list_source_id)
                else:
                    self.pending_urls = self._load_urls(config.PENDING_URLS_FILE, as_list=True)
                    self.visited_urls = self._load_urls(config.VISITED_URLS_FILE)
                    pending = list(self.pending_urls)
                
                if not pending:
                    logger.info("待处理队列为空，跳过提取")
                else:
                    try:
                        self.extract_magnets(keep_browser_open=True)
                    except BlockedException:
                        logger.error("提取时被拦截，停止自动模式")
                        raise
                    except Exception as e:
                        logger.error(f"提取出错: {e}")
                        logger.debug(f"错误详情: {traceback.format_exc()}")
                
                if self.store is not None:
                    pending = self.store.get_pending_urls(self.list_source_id)
                else:
                    pending = self._load_urls(config.PENDING_URLS_FILE, as_list=True)
                
                if not pending:
                    logger.info(f"第 {iteration} 轮完成，队列已清空")
                    if max_iterations and iteration >= max_iterations:
                        break
                
                if iteration < max_iter:
                    delay = random.uniform(5, 10)
                    logger.debug(f"等待 {delay:.2f} 秒后开始下一轮...")
                    time.sleep(delay)
        finally:
            logger.info("清理资源...")
            if self.page:
                try:
                    self._close_browser()
                    self.page = None
                    logger.info("浏览器已关闭")
                except Exception as e:
                    logger.warning(f"关闭浏览器时出错: {e}")
        
        if not self.store and config.MAGNETS_LIST_FILE.exists():
            try:
                config.COMPLETED_DIR.mkdir(parents=True, exist_ok=True)
                dest = config.COMPLETED_DIR / config.MAGNETS_LIST_FILE.name
                shutil.move(str(config.MAGNETS_LIST_FILE), str(dest))
                logger.info(f"已将 {config.MAGNETS_LIST_FILE.name} 移动到 {config.COMPLETED_DIR.name}/")
            except Exception as e:
                logger.error(f"移动文件失败: {e}")
                logger.debug(f"错误详情: {traceback.format_exc()}")
        
        logger.info("=" * 60)
        logger.info(f"自动模式完成，共 {iteration} 轮")
        logger.info("=" * 60)


def main():
    # 从 DB settings 读运行时覆盖值（crawl_delay 等）
    try:
        config.get_settings_override()
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="磁力链接爬虫 (JavDB)")
    parser.add_argument("--base-url", type=str, default=None, help="JavDB 网站地址，覆盖 config.py 中的 BASE_URL")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    def add_list_args(p):
        p.add_argument("--list-code", "-l", type=str, default=None, help="列表代码，如 SDMM（使用 DB 时必填）")
        p.add_argument("--list-source-id", type=int, default=None, help="列表源 ID（与 list-code 二选一）")
        p.add_argument("--actor-name", type=str, default=None, help="演员名（演员搜索时用于详情页验证）")

    scan_parser = subparsers.add_parser("scan", help="扫描列表页获取详情链接")
    scan_parser.add_argument("--pages", "-p", type=int, default=None, help="扫描页数上限")
    scan_parser.add_argument("--update", "-u", action="store_true", help="更新模式：从第1页起扫，遇本页无新任务即停止")
    scan_parser.add_argument("--visible", "-v", action="store_true", help="显示浏览器")
    add_list_args(scan_parser)

    extract_parser = subparsers.add_parser("extract", help="从队列中提取详情页磁力")
    extract_parser.add_argument("--visible", "-v", action="store_true", help="显示浏览器")
    extract_parser.add_argument("--limit", type=int, default=None, help="最多处理条数")
    extract_parser.add_argument("--failed-only", action="store_true", help="仅重试失败任务")
    add_list_args(extract_parser)

    auto_parser = subparsers.add_parser("auto", help="自动连续执行：扫描 -> 提取")
    auto_parser.add_argument("--pages", "-p", type=int, default=None, help="每轮扫描页数上限")
    auto_parser.add_argument("--iterations", "-i", type=int, default=None, help="最大循环次数")
    auto_parser.add_argument("--visible", "-v", action="store_true", help="显示浏览器")
    add_list_args(auto_parser)

    # 排行榜爬取子命令
    ranking_parser = subparsers.add_parser("ranking", help="爬取排行榜")
    ranking_parser.add_argument("--rank-type", type=str, default="daily", help="排行类型: daily/weekly/monthly/actor")
    ranking_parser.add_argument("--max-pages", type=int, default=5, help="最大翻页数")
    ranking_parser.add_argument("--visible", "-v", action="store_true", help="显示浏览器")
    ranking_parser.add_argument("--add-tasks", action="store_true", help="同时将排行影片加入任务队列")

    # 演员爬取子命令
    actor_parser = subparsers.add_parser("crawl-actor", help="爬取演员详情页")
    actor_parser.add_argument("--actor-url", type=str, default="", help="演员详情页 URL")
    actor_parser.add_argument("--actor-name", type=str, default="", help="演员名字（自动搜索匹配）")
    actor_parser.add_argument("--visible", "-v", action="store_true", help="显示浏览器")

    # 单任务提取
    single_parser = subparsers.add_parser("extract-single", help="提取单个 URL 的磁力链接")
    single_parser.add_argument("--url", type=str, required=True, help="详情页 URL")
    single_parser.add_argument("--visible", "-v", action="store_true", help="显示浏览器")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    # 覆盖 BASE_URL
    if getattr(args, "base_url", None):
        config.BASE_URL = args.base_url.rstrip("/")
        logger.info(f"使用自定义 JavDB 地址: {config.BASE_URL}")

    use_db = getattr(args, "list_code", None) or getattr(args, "list_source_id", None)
    # ranking 和 crawl-actor 命令始终需要数据库存储
    if not use_db and args.command in ("ranking", "crawl-actor", "extract-single"):
        use_db = True
    store = None
    list_source_id = None
    list_path = config.LIST_PATH
    list_params = config.LIST_PARAMS
    _pages_arg = getattr(args, "pages", None)
    max_pages = _pages_arg if _pages_arg is not None else config.MAX_PAGES

    if use_db:
        logger.info("使用数据库模式")
        store = SqliteTaskStore(Path(config.DB_PATH))
        if args.command in ("ranking", "crawl-actor", "extract-single"):
            # 这些命令只需要 store，不需要 list_source
            logger.info(f"{args.command} 模式，跳过 list_source 初始化")
        elif getattr(args, "list_source_id", None):
            logger.info(f"使用list_source_id: {args.list_source_id}")
            row = store.get_list_source_by_id(args.list_source_id)
            if not row:
                logger.error(f"未找到该 list_source_id: {args.list_source_id}")
                return
            list_source_id = row["id"]
            list_path = row["list_path"]
            list_params = row["list_params"]
            if row["max_pages"] != 0 and _pages_arg is None:
                max_pages = row["max_pages"]
            logger.info(f"列表配置: list_path={list_path}, list_params={list_params}, max_pages={max_pages}")
        else:
            logger.info(f"使用list_code: {args.list_code}")
            row = store.ensure_list_source(args.list_code, max_pages=max_pages)
            list_source_id = row["id"]
            list_path = row["list_path"]
            list_params = row["list_params"]
            if _pages_arg is None and row["max_pages"] != 0:
                max_pages = row["max_pages"]
            logger.info(f"列表配置: list_path={list_path}, list_params={list_params}, max_pages={max_pages}")
    else:
        logger.info("使用文件模式")

    _actor_name = getattr(args, "actor_name", None)

    # 如果有 list_source_id，尝试从数据库获取 actor_name
    if _actor_name is None and use_db and list_source_id is not None:
        store = SqliteTaskStore(Path(config.DB_PATH))
        row = store.get_list_source_by_id(list_source_id)
        if row:
            _actor_name = row.get("actor_name")
        logger.info(f"从数据库获取 actor_name: {_actor_name}")

    scraper = MagnetScraper(
        max_pages=max_pages,
        visible=args.visible,
        store=store,
        list_source_id=list_source_id,
        list_path=list_path,
        list_params=list_params,
        actor_name=_actor_name,
    )

    list_code = scraper._list_code()
    crawl_type = "extract_failed" if (
        getattr(args, "command", None) == "extract" and getattr(args, "failed_only", False)
    ) else getattr(args, "command", "extract")
    _notify_backend_register(list_code, crawl_type)
    atexit.register(_notify_backend_unregister)

    try:
        max_restarts = 5
        restart_count = 0
        logger.info(f"开始执行命令: {args.command}")
        
        while restart_count < max_restarts:
            try:
                if args.command == "scan":
                    logger.info("执行扫描命令...")
                    scraper.scan_list_pages(update_mode=getattr(args, "update", False))
                elif args.command == "extract":
                    logger.info("执行提取命令...")
                    scraper.extract_magnets(
                        limit=getattr(args, "limit", None),
                        failed_only=getattr(args, "failed_only", False),
                    )
                elif args.command == "auto":
                    logger.info("执行自动模式命令...")
                    scraper.auto_run(max_iterations=getattr(args, "iterations", None))
                elif args.command == "ranking":
                    rank_type = getattr(args, "rank_type", "daily")
                    logger.info(f"执行排行榜爬取: {rank_type}")
                    from ranking_scraper import RankingScraper
                    r = RankingScraper(scraper)
                    if rank_type == "actor":
                        entries = r.crawl_actor_ranking(
                            max_pages=getattr(args, "max_pages", 3),
                        )
                        if entries:
                            saved = r.save_actor_rankings(entries)
                            logger.info(f"演员排行保存完成: {saved}")
                    else:
                        entries = r.crawl_ranking(
                            rank_type=rank_type,
                            max_pages=getattr(args, "max_pages", 5),
                        )
                        if entries:
                            # 1. 入库 rankings 表 + 创建 pending task（不爬详情）
                            saved = r.save_and_add_tasks(entries, rank_type)
                            logger.info(f"排行榜入库完成: {saved}")
                            # 2. 设 scraper 的 list_source_id 为 RANKING，
                            #    这样 extract_magnets 能查到排行榜创建的 pending task
                            ranking_src = scraper.store.ensure_list_source(
                                "RANKING", list_path="/rankings", max_pages=100)
                            scraper.list_source_id = ranking_src["id"]
                            # 3. 自动触发 extract（复用影视库的完整提取逻辑）
                            #    处理所有 pending task（含排行榜创建的）:
                            #    process_detail_page → mark_visited + 演员关联 + mark_failed
                            logger.info("开始提取排行榜详情页（复用 extract 逻辑）...")
                            scraper.extract_magnets()
                            logger.info("排行榜详情提取完成")
                elif args.command == "crawl-actor":
                    from actor_scraper import ActorScraper
                    a = ActorScraper(scraper)
                    actor_url = getattr(args, "actor_url", "")
                    actor_name = getattr(args, "actor_name", "")
                    if not actor_url and actor_name:
                        logger.info(f"通过名字搜索演员: {actor_name}")
                        results = a.search_actor(actor_name)
                        if results:
                            actor_url = results[0]["detail_url"]
                            logger.info(f"找到演员: {results[0]['name']} -> {actor_url}")
                        else:
                            logger.error(f"未找到演员: {actor_name}")
                            break
                    if not actor_url:
                        logger.error("请提供 --actor-url 或 --actor-name")
                        break
                    logger.info(f"执行演员爬取: {actor_url}")
                    result = a.crawl_actor_full(actor_url)
                    logger.info(f"演员爬取完成: {result}")
                elif args.command == "extract-single":
                    single_url = getattr(args, "url", "")
                    if not single_url:
                        logger.error("请提供 --url")
                        break
                    logger.info(f"提取单个任务: {single_url}")
                    scraper._process_single_url(single_url)
                logger.info("命令执行成功")
                break
            except BlockedException as e:
                restart_count += 1
                logger.error(f"检测到拦截，第 {restart_count}/{max_restarts} 次重启...")
                logger.error(f"拦截详情: {e}")
                if restart_count < max_restarts:
                    wait_time = 120 * restart_count
                    logger.info(f"等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                continue
            except Exception as e:
                logger.error(f"执行命令时出错: {e}")
                logger.error(f"错误详情: {traceback.format_exc()}")
                break
        
        if restart_count >= max_restarts:
            logger.error("已达最大重启次数，请检查网络或更换 IP")
    finally:
        logger.info("清理资源...")
        _notify_backend_unregister()
        try:
            atexit.unregister(_notify_backend_unregister)
        except Exception:
            pass
        logger.info("程序结束")


if __name__ == "__main__":
    main()
