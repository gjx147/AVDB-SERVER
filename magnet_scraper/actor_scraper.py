"""演员爬虫 —— 搜索演员、爬取演员详情页和作品列表。

由 scraper.py main() 的 crawl-actor 子命令调用：
    a = ActorScraper(scraper)
    results = a.search_actor(name)        # 搜索演员
    result = a.crawl_actor_full(url)      # 完整爬取演员信息+作品

复用 scraper 的 Playwright 浏览器实例和 store 数据层。
"""

from __future__ import annotations

import logging
import random
import time
from urllib.parse import quote, urljoin

import config

logger = logging.getLogger(__name__)

# 演员分类页路径（搜索结果中过滤掉这些）
_ACTOR_CATEGORY_PATHS = {
    "/actors/censored", "/actors/uncensored",
    "/actors/western", "/actors/recommend", "/actors/anime",
}


class ActorScraper:
    """演员爬虫，复用 MagnetScraper 的浏览器和 store。"""

    def __init__(self, scraper):
        self.scraper = scraper
        self.page = scraper.page
        self.store = scraper.store
        self.BASE_URL = config.BASE_URL

    def _ensure_browser(self):
        """确保浏览器已初始化。"""
        if not self.page:
            self.scraper.init_browser()
            self.page = self.scraper.page

    def resolve_actor_url_from_tasks(self, actor_name: str) -> str:
        """搜索页被 Cloudflare 拦截时的兜底：从该演员已关联的作品详情页提取演员 URL。

        详情页 Cloudflare 审查宽松（已验证能过），作品详情页的面板块里有
        a[href^='/actors/'] 链接。匹配演员名即可拿到 /actors/<hash> URL。

        返回演员详情页 URL，找不到返回空字符串。
        """
        self._ensure_browser()
        try:
            with self.store._conn() as conn:
                # 查该演员关联的 task URL（取最近 5 个，详情页可能失效）
                rows = conn.execute(
                    "SELECT t.url FROM tasks t "
                    "JOIN actor_movies am ON am.task_id = t.id "
                    "JOIN actors a ON a.id = am.actor_id "
                    "WHERE a.name = ? AND t.url IS NOT NULL AND t.url != '' "
                    "AND t.url NOT LIKE 'pending://%' "
                    "ORDER BY t.id DESC LIMIT 5",
                    (actor_name,)
                ).fetchall()
            task_urls = [r["url"] for r in rows] if rows else []
        except Exception as e:
            logger.debug(f"查询演员关联作品失败: {e}")
            task_urls = []

        if not task_urls:
            logger.info(f"演员 {actor_name} 无已关联作品，无法从详情页提取 URL")
            return ""

        logger.info(f"尝试从 {len(task_urls)} 个已关联作品详情页提取演员 URL")
        for task_url in task_urls:
            try:
                logger.debug(f"访问作品详情页提取演员链接: {task_url}")
                self.page.goto(task_url, wait_until="domcontentloaded", timeout=60000)
                self.scraper._handle_security_check()
                time.sleep(random.uniform(1.5, 3))

                # 详情页面板块里的演员链接
                links = self.page.locator("a[href^='/actors/']").all()
                for link in links:
                    try:
                        href = link.get_attribute("href") or ""
                        if any(cat in href for cat in _ACTOR_CATEGORY_PATHS):
                            continue
                        link_text = (link.inner_text() or "").strip()
                        # 匹配演员名（完全匹配或包含）
                        if link_text and (link_text == actor_name or actor_name in link_text or link_text in actor_name):
                            resolved = urljoin(self.BASE_URL, href)
                            logger.info(f"从详情页 {task_url} 提取到演员 URL: {resolved} (匹配名: {link_text})")
                            return resolved
                    except Exception:
                        continue
            except Exception as e:
                logger.debug(f"访问详情页 {task_url} 失败: {e}")
                continue

        logger.info(f"从 {len(task_urls)} 个详情页都未匹配到演员 {actor_name} 的链接")
        return ""

    def search_actor(self, keyword: str) -> list:
        """搜索演员，返回 [{name, detail_url, avatar_url}]。

        搜索页 Cloudflare 审查比首页/详情页更严，直接 goto 常被拦截。
        策略：先访问主页「暖场」拿到 cf_clearance cookie，再导航到搜索页。
        """
        self._ensure_browser()

        search_url = f"{self.BASE_URL}/search?f=actor&q={quote(keyword)}"
        logger.info(f"搜索演员: {keyword} -> {search_url}")

        try:
            # 关键修复：先访问主页暖场（拿到 cf_clearance cookie），再 goto 搜索页
            # 直接 goto 搜索页会被 Cloudflare 拦截（搜索页审查更严，Turnstile
            # 常不渲染可点击元素，导致 _handle_security_check 误判失败）
            try:
                logger.debug("搜索前先访问主页暖场拿 cf_clearance...")
                self.page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=60000)
                self.scraper._handle_security_check()
                time.sleep(random.uniform(1.5, 3))
                logger.info("主页暖场完成，开始导航到搜索页")
            except Exception as e:
                logger.debug(f"主页暖场异常（忽略，继续尝试搜索）: {e}")

            # 带 cookie 导航到搜索页（暖场拿到的 cf_clearance 会自动带上）
            self.page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            self.scraper._handle_security_check()
            time.sleep(random.uniform(2, 4))

            results = []
            links = self.page.locator("a[href^='/actors/']").all()
            logger.info(f"搜索结果找到 {len(links)} 个演员链接")

            for link in links:
                try:
                    href = link.get_attribute("href") or ""
                    # 过滤分类页
                    if any(cat in href for cat in _ACTOR_CATEGORY_PATHS):
                        continue

                    name = (link.inner_text() or "").strip()
                    if not name or len(name) < 2:
                        continue

                    # 过滤无关关键词
                    if any(kw in name.lower() for kw in
                           ["评论", "喜欢", "收藏", "下载", "search", "category"]):
                        continue

                    avatar_url = None
                    try:
                        img = link.locator("img").first
                        if img.count() > 0:
                            avatar_url = img.get_attribute("src") or ""
                    except Exception:
                        pass

                    results.append({
                        "name": name,
                        "detail_url": urljoin(self.BASE_URL, href),
                        "avatar_url": avatar_url,
                    })
                except Exception:
                    continue

            # 去重
            seen = set()
            deduped = []
            for r in results:
                if r["name"] not in seen:
                    seen.add(r["name"])
                    deduped.append(r)

            logger.info(f"演员搜索 {keyword}: 找到 {len(deduped)} 位")
            return deduped

        except Exception as e:
            logger.error(f"搜索演员失败: {e}")
            return []

    def crawl_actor_full(self, actor_url: str) -> dict:
        """完整爬取演员信息 + 作品列表。

        1. 爬取演员详情页（姓名/头像/身高/罩杯等）
        2. 翻页爬取演员作品列表
        3. 入库演员 + 创建 pending task

        返回 {actor, actor_id, movie_count, tasks_added}
        """
        self._ensure_browser()

        logger.info(f"开始完整爬取演员: {actor_url}")
        self.scraper._write_crawl_status(
            phase="actor", crawl_type="actor", actor_url=actor_url,
        )

        # 1. 爬取演员信息
        info = self.crawl_actor_info(actor_url)
        name = info.get("name")
        if not name:
            logger.error(f"无法提取演员名称: {actor_url}")
            return {"actor": None, "actor_id": None, "movie_count": 0, "tasks_added": 0}

        logger.info(f"演员信息: {name}")

        # 2. 爬取作品列表
        movies = self.crawl_actor_movies(actor_url, max_pages=50)
        logger.info(f"演员 {name} 作品列表: {len(movies)} 部")

        # 3. 入库演员（source_url 存 JavDB 演员页 URL，note 保留兼容）
        actor_id = self.store.upsert_actor(
            name,
            source_url=actor_url,
            avatar_url=info.get("avatar_url"),
            gender=info.get("gender"),
            birth_date=info.get("birth_date"),
            height=info.get("height"),
            cup=info.get("cup"),
            measurements=info.get("measurements"),
            debut_date=info.get("debut_date"),
            movie_count=len(movies),
            note=f"source_url: {actor_url}",
        )

        # 4. 创建列表源 + pending tasks
        # 列表源名: ACTOR_{name}（截取前20字符避免过长）
        list_code = f"ACTOR_{name[:20]}".upper()
        src = self.store.ensure_list_source(list_code, list_path=actor_url, max_pages=100)

        tasks_added = 0
        for movie_url in movies:
            if not self.store.task_exists_with_url(movie_url):
                self.store.add_pending_urls(src["id"], [movie_url])
                tasks_added += 1
            # 建立 actor↔task 关联（无论新建还是已存在，确保演员详情页作品列表有数据）
            task_row = self.store.get_task_by_url(movie_url)
            if task_row:
                self.store.link_actor_movie(actor_id, task_row["id"])

        logger.info(f"演员 {name} 入库完成: actor_id={actor_id}, movies={len(movies)}, tasks_added={tasks_added}")

        return {
            "actor": name,
            "actor_id": actor_id,
            "movie_count": len(movies),
            "tasks_added": tasks_added,
        }

    def crawl_actor_info(self, actor_url: str) -> dict:
        """爬取演员详情页，提取基本信息。"""
        info = {}
        try:
            self.page.goto(actor_url, wait_until="domcontentloaded", timeout=60000)
            self.scraper._handle_security_check()
            time.sleep(random.uniform(2, 4))

            # 姓名
            try:
                name_el = self.page.locator(".actor-name, h2, .name, .title").first
                if name_el.count() > 0:
                    info["name"] = (name_el.inner_text() or "").strip()
            except Exception:
                pass

            # 头像（JavDB 演员页头像在 .avatar img，URL 含 /avatars/ 路径）
            # 注意：不能用 .cover img，它会误匹配到影片封面（/covers/ 路径）
            try:
                img = self.page.locator(".avatar img, .actor-photo img, img.avatar, .actor-header img, header img").first
                if img.count() > 0:
                    src = img.get_attribute("src") or ""
                    # 校验：演员头像 URL 必须含 /avatars/，排除 /covers/（影片封面）
                    if src and "/avatars/" in src:
                        info["avatar_url"] = src
                    elif src and "/covers/" not in src and "/avatars/" not in src:
                        # 未知路径但不是 covers，谨慎接受
                        info["avatar_url"] = src
            except Exception:
                pass

            # 信息面板行（身高/罩杯/出生日期等）
            try:
                rows = self.page.locator(".info-panel .row, .actor-info .item, .panel-section .row").all()
                for row in rows:
                    try:
                        label_el = row.locator(".label, .key, dt").first
                        value_el = row.locator(".value, dd").first
                        label = (label_el.inner_text() or "").strip() if label_el.count() > 0 else ""
                        value = (value_el.inner_text() or "").strip() if value_el.count() > 0 else ""

                        if not label or not value:
                            full_text = (row.inner_text() or "").strip()
                            if ":" in full_text:
                                parts = full_text.split(":", 1)
                                label, value = parts[0].strip(), parts[1].strip()
                            elif "：" in full_text:
                                parts = full_text.split("：", 1)
                                label, value = parts[0].strip(), parts[1].strip()

                        label_lower = label.lower()
                        if any(kw in label_lower for kw in ["生日", "出生", "birth"]):
                            info["birth_date"] = value
                        elif any(kw in label_lower for kw in ["身高", "height"]):
                            info["height"] = value
                        elif any(kw in label_lower for kw in ["罩杯", "cup"]):
                            info["cup"] = value
                        elif any(kw in label_lower for kw in ["三围", "measurements"]):
                            info["measurements"] = value
                        elif any(kw in label_lower for kw in ["出道", " debut"]):
                            info["debut_date"] = value
                    except Exception:
                        continue
            except Exception:
                pass

        except Exception as e:
            logger.error(f"爬取演员信息失败: {e}")

        # 推断 gender：有罩杯信息的是女演员（javdb 男优页没有 cup 字段）
        if info.get("cup"):
            info["gender"] = "female"
        elif any(kw in actor_url for kw in ["/censored", "/uncensored"]):
            # 有码/无码演员榜爬来的都是女性
            info["gender"] = "female"

        return info

    def crawl_actor_movies(self, actor_url: str, max_pages: int = 50) -> list:
        """翻页爬取演员作品列表，返回详情页 URL 列表。"""
        all_urls = []
        page_num = 1
        base_url = actor_url.rstrip("/")

        while page_num <= max_pages:
            url = base_url if page_num == 1 else f"{base_url}?page={page_num}"
            logger.info(f"爬取演员作品第 {page_num} 页: {url}")

            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
                self.scraper._handle_security_check()
                time.sleep(random.uniform(2, 4))

                links = self.page.locator("a[href^='/v/']").all()
                if not links:
                    logger.info(f"第 {page_num} 页无作品链接，停止")
                    break

                page_urls = []
                for link in links:
                    try:
                        href = link.get_attribute("href") or ""
                        if href:
                            full_url = urljoin(self.BASE_URL, href)
                            page_urls.append(full_url)
                    except Exception:
                        continue

                if not page_urls:
                    break

                # 去重（当前页内）
                seen = set(page_urls)
                all_urls.extend(page_urls)
                logger.info(f"第 {page_num} 页提取 {len(page_urls)} 部作品，累计 {len(all_urls)} 部")

                self.scraper._write_crawl_status(
                    phase="actor_movies", crawl_type="actor",
                    page_current=page_num, page_max=max_pages,
                    items_found=len(all_urls),
                )

                page_num += 1
                time.sleep(random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX))

            except Exception as e:
                logger.error(f"爬取演员作品第 {page_num} 页失败: {e}")
                break

        # 全局去重
        return list(dict.fromkeys(all_urls))
