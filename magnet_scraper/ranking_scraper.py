"""排行榜爬虫 —— 爬取 JavDB 排行榜页面，提取条目入库。

由 scraper.py main() 的 ranking 子命令调用：
    r = RankingScraper(scraper)
    entries = r.crawl_ranking(rank_type, max_pages)
    r.save_and_add_tasks(entries, rank_type)
    r.crawl_ranking_details(entries)

复用 scraper 的 Playwright 浏览器实例和 store 数据层。
"""

from __future__ import annotations

import logging
import random
import time
from urllib.parse import urljoin

import config

logger = logging.getLogger(__name__)

# JavDB 排行榜 URL 映射（rank_type → 路径参数）
# 正确地址（用户确认）：
#   日榜: /rankings/movies?p=daily&t=censored
#   周榜: /rankings/movies?p=weekly&t=censored
#   月榜: /rankings/movies?p=monthly&t=censored
#   演员月榜: /rankings/actors?t=censored (由 crawl_actor_ranking 处理)
RANKING_URLS = {
    "daily": "/rankings/movies?p=daily&t=censored",
    "weekly": "/rankings/movies?p=weekly&t=censored",
    "monthly": "/rankings/movies?p=monthly&t=censored",
}


class RankingScraper:
    """排行榜爬虫，复用 MagnetScraper 的浏览器和 store。"""

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

    def crawl_ranking(self, rank_type: str = "hot", max_pages: int = 5) -> list:
        """爬取排行榜列表页，返回条目列表。

        每个条目: {rank_position, detail_url, cover_url, title, video_code, score, views}
        """
        path = RANKING_URLS.get(rank_type, RANKING_URLS["daily"])
        self._ensure_browser()

        all_entries = []
        page_num = 1
        global_pos = 0  # 跨页累计排名

        logger.info(f"开始爬取排行榜: {rank_type} (最多 {max_pages} 页)")
        self.scraper._write_crawl_status(
            phase="ranking", crawl_type="ranking", rank_type=rank_type,
            page_current=1, page_max=max_pages, items_found=0,
        )

        while page_num <= max_pages:
            url = f"{self.BASE_URL}{path}"
            if page_num > 1:
                url += f"&page={page_num}"

            logger.info(f"爬取排行榜第 {page_num}/{max_pages} 页: {url}")

            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=90000)
                self.scraper._handle_security_check()
                # 等待页面 JS 渲染完成（排行榜条目是动态加载的）
                time.sleep(random.uniform(3, 5))

                # 提取排行榜条目
                entries = self._extract_ranking_items(page_num)
                if not entries:
                    logger.info(f"第 {page_num} 页无条目，停止爬取")
                    break

                # 设置跨页全局排名
                for e in entries:
                    global_pos += 1
                    e["rank_position"] = global_pos

                all_entries.extend(entries)
                logger.info(f"第 {page_num} 页提取 {len(entries)} 条，累计 {len(all_entries)} 条")

                self.scraper._write_crawl_status(
                    phase="ranking", crawl_type="ranking", rank_type=rank_type,
                    page_current=page_num, page_max=max_pages,
                    items_found=len(all_entries),
                )

                page_num += 1
                time.sleep(random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX))

            except Exception as e:
                logger.error(f"爬取排行榜第 {page_num} 页失败: {e}")
                break

        logger.info(f"排行榜爬取完成: {rank_type} 共 {len(all_entries)} 条")
        return all_entries

    def _extract_ranking_items(self, page_num: int) -> list:
        """从当前页面提取排行榜条目。"""
        entries = []

        # JavDB 排行榜条目选择器（按优先级排序）
        # 页面结构: <div class="movie-list"> <div class="item"> <a href="/v/xxx">...
        selectors = [
            ".movie-list .item",
            ".movie-list > .item",
            ".grid-item",
            ".movie-item",
            ".box-item",
            ".movie-list .box",
            ".grid-space .item",
            "a[href^='/v/']",
        ]

        # 先等 /v/ 链接出现（最多等 10 秒），确认 JS 已渲染
        try:
            self.page.wait_for_selector("a[href*='/v/']", timeout=10000)
            logger.debug("页面已渲染，检测到 /v/ 链接")
        except Exception:
            logger.warning("等待 /v/ 链接超时，页面可能未渲染")
            # 尝试打印页面 HTML 前 1000 字符用于调试
            try:
                html_snippet = self.page.content()[:1000]
                logger.debug(f"页面 HTML 前1000字符: {html_snippet}")
            except Exception:
                pass

        items = []
        used_selector = None
        for sel in selectors:
            try:
                items = self.page.locator(sel).all()
                if items:
                    used_selector = sel
                    logger.info(f"选择器 '{sel}' 匹配到 {len(items)} 个元素")
                    break
            except Exception:
                continue

        if not items:
            logger.warning("未找到任何排行榜条目元素")
            return []

        for item in items:
            try:
                entry = self._parse_ranking_item(item)
                if entry and entry.get("video_code"):
                    entries.append(entry)
            except Exception as e:
                logger.debug(f"解析条目失败: {e}")
                continue

        # 去重（按 video_code）
        seen = set()
        deduped = []
        for e in entries:
            vc = e["video_code"]
            if vc not in seen:
                seen.add(vc)
                deduped.append(e)

        logger.info(f"提取到 {len(deduped)} 个有效条目（从 {len(entries)} 个中去重）")
        return deduped

    def _parse_ranking_item(self, item) -> dict | None:
        """解析单个排行榜条目元素。"""
        entry = {}

        # 提取链接和番号
        try:
            # 如果 item 本身就是 <a> 标签
            tag_name = ""
            try:
                tag_name = item.evaluate("el => el.tagName")
            except Exception:
                pass

            link = None
            if tag_name == "A":
                link = item
            else:
                # 在 item 内查找详情页链接
                link = item.locator("a[href*='/v/']").first
                if not link or link.count() == 0:
                    link = item.locator("a").first

            if link and link.count() > 0:
                href = link.get_attribute("href") or ""
                if href:
                    entry["detail_url"] = urljoin(self.BASE_URL, href)
                    # 番号从 URL 提取: /v/ABC-123
                    parts = href.rstrip("/").split("/")
                    if len(parts) > 0:
                        entry["video_code"] = parts[-1].upper()
        except Exception:
            return None

        if not entry.get("video_code"):
            return None

        # 提取标题
        try:
            title_el = item.locator(".title, .video-title, [title]").first
            if title_el.count() > 0:
                entry["title"] = (title_el.inner_text() or title_el.get_attribute("title") or "").strip()
        except Exception:
            pass

        # 提取封面图
        try:
            img = item.locator("img").first
            if img.count() > 0:
                entry["cover_url"] = img.get_attribute("src") or img.get_attribute("data-src") or ""
        except Exception:
            pass

        # 提取评分
        try:
            score_el = item.locator(".score, .rating, .value").first
            if score_el.count() > 0:
                score_text = (score_el.inner_text() or "").strip()
                # 解析数字
                import re
                m = re.search(r"(\d+\.?\d*)", score_text)
                if m:
                    entry["score"] = float(m.group(1))
        except Exception:
            pass

        # 提取浏览数
        try:
            meta_el = item.locator(".meta, .views, .info").first
            if meta_el.count() > 0:
                meta_text = (meta_el.inner_text() or "").strip()
                import re
                m = re.search(r"([\d,]+)", meta_text.replace(",", ""))
                if m:
                    entry["views"] = int(m.group(1).replace(",", ""))
        except Exception:
            pass

        return entry

    def save_and_add_tasks(self, entries: list, rank_type: str) -> dict:
        """保存排行榜条目到数据库，并为每条创建 pending task。

        返回 {rankings_saved, tasks_added}
        """
        if not entries:
            return {"rankings_saved": 0, "tasks_added": 0}

        # 保存到 rankings 表
        saved = self.store.save_rankings(entries, rank_type)
        logger.info(f"排行榜保存: {saved} 条入库")

        # 为每条创建 pending task（列表源 RANKING）
        src = self.store.ensure_list_source("RANKING", list_path="/rankings", max_pages=100)
        tasks_added = 0
        for e in entries:
            detail_url = e.get("detail_url")
            if detail_url and not self.store.task_exists_with_url(detail_url):
                self.store.add_pending_urls(src["id"], [detail_url])
                tasks_added += 1

        logger.info(f"排行榜入库: rankings={saved}, tasks_added={tasks_added}")
        return {"rankings_saved": saved, "tasks_added": tasks_added}

    def crawl_ranking_details(self, entries: list) -> dict:
        """对排行榜条目爬取详情页，提取完整元数据和磁力。

        回填 rankings.task_id。
        返回 {success, failed}
        """
        if not entries:
            return {"success": 0, "failed": 0}

        self._ensure_browser()
        total = len(entries)
        success = 0
        failed = 0
        matches = []  # (detail_url, task_id) 用于回填

        logger.info(f"开始爬取排行榜详情页: {total} 条")

        for i, e in enumerate(entries):
            detail_url = e.get("detail_url")
            vc = e.get("video_code", "")
            if not detail_url:
                continue

            self.scraper._write_crawl_status(
                phase="ranking_detail", crawl_type="ranking_detail",
                current_index=i + 1, total=total, current_video_code=vc,
            )

            logger.info(f"排行榜详情 [{i+1}/{total}]: {vc}")

            try:
                delay = random.uniform(config.DETAIL_DELAY_MIN, config.DETAIL_DELAY_MAX)
                time.sleep(delay)

                result = self.scraper.process_detail_page(detail_url)
                ok = result[0]
                best_magnet = result[1]
                magnets_json = result[2]
                video_code = result[3]
                title = result[4]
                poster_url = result[5]
                thumbnails_json = result[6]
                synopsis = result[7]
                actors = result[8]
                err_msg = result[9]

                if ok and best_magnet:
                    extra = getattr(self.scraper, "_last_extra_meta", {}) or {}
                    self.store.mark_visited(
                        detail_url,
                        best_magnet=best_magnet, magnets_json=magnets_json,
                        video_code=video_code, title=title, poster_url=poster_url,
                        thumbnail_urls=thumbnails_json, synopsis=synopsis,
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
                    # 获取 task_id 用于回填
                    task_row = self.store.get_task_by_url(detail_url)
                    if task_row:
                        matches.append((detail_url, task_row["id"]))
                    success += 1
                    logger.info(f"✓ {vc}: 详情提取成功")
                else:
                    self.store.mark_failed(detail_url, err_msg or "详情页提取失败")
                    failed += 1
                    logger.warning(f"✗ {vc}: {err_msg}")

            except Exception as ex:
                failed += 1
                logger.error(f"✗ {vc}: 详情页异常 - {ex}")
                try:
                    self.store.mark_failed(detail_url, str(ex)[:500])
                except Exception:
                    pass

        # 回填 rankings.task_id
        if matches:
            updated = self.store.update_ranking_task_ids(matches)
            logger.info(f"回填 rankings.task_id: {updated} 条")

        logger.info(f"排行榜详情爬取完成: 成功 {success}, 失败 {failed}")
        return {"success": success, "failed": failed}

    def crawl_actor_ranking(self, max_pages: int = 3) -> list:
        """爬取演员排行榜。"""
        self._ensure_browser()
        all_actors = []
        page_num = 1

        logger.info(f"开始爬取演员排行榜 (最多 {max_pages} 页)")

        while page_num <= max_pages:
            url = f"{self.BASE_URL}/rankings/actors?t=censored"
            if page_num > 1:
                url += f"&page={page_num}"

            logger.info(f"爬取演员排行第 {page_num} 页: {url}")

            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=90000)
                self.scraper._handle_security_check()
                time.sleep(random.uniform(2, 4))

                # 提取演员条目
                actors = self._extract_actor_items()
                if not actors:
                    logger.info(f"第 {page_num} 页无演员条目，停止")
                    break

                all_actors.extend(actors)
                logger.info(f"第 {page_num} 页提取 {len(actors)} 位演员，累计 {len(all_actors)} 位")

                page_num += 1
                time.sleep(random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX))

            except Exception as e:
                logger.error(f"爬取演员排行第 {page_num} 页失败: {e}")
                break

        logger.info(f"演员排行爬取完成: 共 {len(all_actors)} 位")
        return all_actors

    def _extract_actor_items(self) -> list:
        """从当前页面提取演员条目。"""
        actors = []

        selectors = [".actor-box a", ".actors a", "a[href^='/actors/']"]
        links = []
        for sel in selectors:
            links = self.page.locator(sel).all()
            if links:
                break

        # 过滤分类页链接
        category_paths = {"/actors/censored", "/actors/uncensored", "/actors/western",
                          "/actors/recommend", "/actors/anime"}

        for link in links:
            try:
                href = link.get_attribute("href") or ""
                if not href or any(cat in href for cat in category_paths):
                    continue

                name = (link.inner_text() or "").strip()
                if not name or len(name) < 2:
                    continue

                # 过滤无关关键词
                if any(kw in name.lower() for kw in ["评论", "喜欢", "收藏", "下载", "search", "category"]):
                    continue

                avatar_url = None
                try:
                    img = link.locator("img").first
                    if img.count() > 0:
                        avatar_url = img.get_attribute("src") or ""
                except Exception:
                    pass

                actors.append({
                    "name": name,
                    "actor_url": urljoin(self.BASE_URL, href),
                    "avatar_url": avatar_url,
                })
            except Exception:
                continue

        # 去重（按名字）
        seen = set()
        deduped = []
        for a in actors:
            if a["name"] not in seen:
                seen.add(a["name"])
                deduped.append(a)

        return deduped

    def save_actor_rankings(self, actors: list) -> int:
        """保存演员排行到数据库。"""
        if not actors:
            return 0

        saved = 0
        for i, a in enumerate(actors):
            try:
                actor_id = self.store.upsert_actor(
                    a["name"],
                    avatar_url=a.get("avatar_url"),
                    note=f"source_url: {a.get('actor_url', '')}",
                )
                if actor_id:
                    saved += 1
            except Exception as e:
                logger.debug(f"保存演员 {a.get('name')} 失败: {e}")

        logger.info(f"演员排行保存: {saved}/{len(actors)} 位")
        return saved
