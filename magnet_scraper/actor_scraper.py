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
import re
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

    def _warmup_homepage(self) -> None:
        """主页暖场：先访问首页拿 cf_clearance cookie。

        搜索页/演员页 Cloudflare 审查比首页严，直接 goto 常被拦截或重置。
        每个爬取会话只暖场一次（_warmed_up 标记去重）。
        """
        if getattr(self, "_warmed_up", False):
            return
        self._ensure_browser()
        try:
            logger.debug("主页暖场：访问首页拿 cf_clearance...")
            self.page.goto(self.BASE_URL, wait_until="domcontentloaded", timeout=60000)
            self.scraper._handle_security_check()
            time.sleep(random.uniform(1.5, 3))
            self._warmed_up = True
            logger.debug("主页暖场完成")
        except Exception as e:
            # 暖场失败不致命：仍交给 _goto_with_retry 的重试去兜底
            logger.debug(f"主页暖场失败（忽略，继续）: {e}")

    def _goto_with_retry(self, url: str, *, max_retries: int = 3, timeout: int = 60000) -> bool:
        """带退避重试的导航。

        goto 失败基本都是网络层瞬时问题（连接关闭/重置/超时/CF 重置），
        故对任意导航异常都退避重试，重试耗尽返回 False。
        导航成功后执行 _handle_security_check 处理 Cloudflare 挑战。
        """
        self._ensure_browser()
        for attempt in range(1, max_retries + 1):
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                self.scraper._handle_security_check()
                time.sleep(random.uniform(2, 4))
                return True
            except Exception as e:
                if attempt >= max_retries:
                    logger.error(f"导航失败(第{attempt}/{max_retries}次),不再重试: {url} -> {e}")
                    return False
                backoff = random.uniform(2, 5) * attempt
                logger.warning(f"导航瞬时失败(第{attempt}/{max_retries}次),{backoff:.1f}s 后重试: {url} -> {e}")
                time.sleep(backoff)
        return False

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

    # ── JavDB 登录（t=s 单体过滤为登录限定功能，未登录访问会被 302 到 /login）──

    def _read_credentials(self) -> tuple[str, str]:
        """从 DB settings 表读 javdb_username / javdb_password。"""
        try:
            with self.store._conn() as conn:
                rows = conn.execute(
                    "SELECT key, value FROM settings WHERE key IN ('javdb_username','javdb_password')"
                ).fetchall()
        except Exception:
            return "", ""
        d = {k: v for k, v in rows}
        return (d.get("javdb_username") or "", d.get("javdb_password") or "")

    def is_logged_in(self) -> bool:
        """检测登录态：未登录页面有 /login 链接，登录后没有。"""
        try:
            self._ensure_browser()
            self._goto_with_retry(f"{self.BASE_URL}/", max_retries=2)
            self.page.wait_for_timeout(2000)
            login_link = self.page.locator("a[href='/login']").first
            return login_link.count() == 0
        except Exception as e:
            logger.warning(f"登录态检测失败（视为未登录）: {e}")
            return False

    def ensure_logged_in(self) -> bool:
        """确保登录：已登录直接返回；否则用配置的账号自动登录。

        返回 True=已登录（t=s 可用）；False=登录失败/未配置（调用方降级）。
        """
        if self.is_logged_in():
            logger.info("JavDB 登录态有效")
            return True
        username, password = self._read_credentials()
        if not username or not password:
            logger.info("未配置 JavDB 账号（settings: javdb_username/javdb_password）")
            return False
        logger.info(f"尝试自动登录 JavDB（账号: {username[:3]}***）")
        try:
            self._ensure_browser()
            if not self._goto_with_retry(f"{self.BASE_URL}/login", max_retries=2):
                logger.warning("登录页导航失败")
                return False
            # 过门：同意页/年龄确认（语言随界面，繁简英都点）
            for sel in ("text=是,我已滿18歲", "text=是,我已满18岁", "text=Yes, I am",
                        "button:has-text('同意')", "button:has-text('Agree')"):
                try:
                    btn = self.page.locator(sel).first
                    if btn.count() > 0 and btn.is_visible():
                        btn.click()
                        self.page.wait_for_timeout(2500)
                except Exception:
                    continue
            if "login" not in (self.page.url or ""):
                self._goto_with_retry(f"{self.BASE_URL}/login", max_retries=2)
            # 等待 Turnstile 自动通过
            for _ in range(10):
                if "login" not in (self.page.url or ""):
                    break
                self.page.wait_for_timeout(2000)
            # 填表（placeholder 双语匹配，与 backend 侧登录会话一致）
            user_field = self.page.locator(
                "input[placeholder*='Username' i], input[placeholder*='用户' i], "
                "input[placeholder*='邮箱' i], input[placeholder*='账号' i], "
                "input[name='username'], input[name='email'], input[name='name'], #username").first
            pass_field = self.page.locator("input[type='password']:visible").first
            if not user_field.count() or not pass_field.count():
                logger.warning("登录表单字段未找到（页面结构可能变化）")
                return False
            user_field.fill(username)
            pass_field.fill(password)
            captcha = self._read_captcha_text() or ""
            cap = self.page.locator(
                "input[placeholder*='Captcha' i], input[placeholder*='验证码'], "
                "input[name='captcha'], #captcha, input[name='code']").first
            if cap.count():
                if captcha:
                    cap.fill(captcha)
                else:
                    logger.warning("需要验证码但无法自动识别——爬虫侧自动登录不可用，将降级")
                    return False
            else:
                logger.info("未出现验证码输入框（可能已跳过）")
            # 7 天免登录
            try:
                keep = self.page.locator("input[type='checkbox']").first
                if keep.count() and not keep.is_checked():
                    keep.check()
            except Exception:
                pass
            self.page.locator(
                "button:has-text('Sign in'), button:has-text('登入'), button:has-text('登录'), "
                "input[type='submit'], button[type='submit']").first.click()
            for _ in range(15):
                self.page.wait_for_timeout(2000)
                if "login" not in (self.page.url or ""):
                    break
            ok = self.is_logged_in()
            logger.info(f"自动登录{'成功' if ok else '失败'}")
            return ok
        except Exception as e:
            logger.warning(f"自动登录异常: {e}")
            return False

    def _read_captcha_text(self) -> str:
        """读取验证码图片文字（预留 OCR 接入点；当前返回空=无法自动识别）。

        登录流程遇验证码：有人工辅助登录（backend 设置页）兜底，
        爬虫侧自动登录在无验证码时可用；带验证码则返回 False 降级。
        """
        return ""

    def crawl_actor_full(self, actor_url: str, actor_id: int | None = None, max_co_star: int = 0, solo_only: bool = False) -> dict:
        """完整爬取演员信息 + 作品列表。

        1. 爬取演员详情页（姓名/头像/身高/罩杯等）
        2. 翻页爬取演员作品列表（max_co_star>0 时逐部核对共演人数，超过上限跳过；
           solo_only=True 只爬单体作品：javdb 演员页 t=s 过滤，如 ?sort_type=0&t=s）
        3. 入库演员 + 创建 pending task

        actor_id：若调用方已知目标演员（如演员库"补齐作品"），直接按 id 更新
        元数据并关联作品，不依赖名字匹配——杜绝因名字差异/污染而新建重复演员。
        max_co_star：最大共演人数限制（0=不限）。

        返回 {actor, actor_id, movie_count, tasks_added}
        """
        self._ensure_browser()

        logger.info(f"开始完整爬取演员: {actor_url}" + (f"（指定 actor_id={actor_id}）" if actor_id else "")
                    + (f"（最大共演 {max_co_star} 人）" if max_co_star > 0 else "")
                    + ("（仅单体作品）" if solo_only else ""))
        if solo_only:
            # t=s 单体过滤需登录；登录成功用 URL 过滤（省一半详情页访问），
            # 失败/未配置账号则降级为详情页数女演员过滤（max_co_star=1，慢但可用）
            if self.ensure_logged_in():
                logger.info("已登录：单体过滤使用 t=s 列表过滤")
            else:
                logger.warning("未登录：单体过滤降级为详情页女演员数过滤（较慢）")
                solo_only = False
                max_co_star = 1
        self.scraper._write_crawl_status(
            phase="actor", crawl_type="actor", actor_url=actor_url,
        )

        # 0. 主页暖场：先访问首页拿 cf_clearance（演员页 Cloudflare 审查较严）
        self._warmup_homepage()

        # 1. 爬取演员信息 + 2. 作品列表
        info = self.crawl_actor_info(actor_url)
        movies = self.crawl_actor_movies(actor_url, max_pages=50, max_co_star=max_co_star, solo_only=solo_only)
        logger.info(f"演员作品列表: {len(movies)} 部")

        # 可刷新的元数据（剔除 None，避免覆盖该演员已有的好数据）
        meta = {
            k: v for k, v in {
                "source_url": actor_url,
                "avatar_url": info.get("avatar_url"),
                "gender": info.get("gender"),
                "birth_date": info.get("birth_date"),
                "height": info.get("height"),
                "cup": info.get("cup"),
                "measurements": info.get("measurements"),
                "debut_date": info.get("debut_date"),
                "movie_count": len(movies),
                "works_fetched": 1,  # 作品已补齐标记：「全部补齐」下次跳过该演员
            }.items() if v is not None
        }

        # 3. 入库/更新演员
        if actor_id:
            # 指定 id：直接更新该演员元数据，按 id 关联作品（不依赖名字匹配，杜绝重复演员）
            self.store.update_actor_meta(actor_id, **meta)
            name = info.get("name") or f"actor_{actor_id}"
            logger.info(f"按指定 actor_id={actor_id} 更新元数据完成: {name}")
        else:
            name = info.get("name")
            if not name:
                logger.error(f"无法提取演员名称: {actor_url}")
                return {"actor": None, "actor_id": None, "movie_count": 0, "tasks_added": 0}
            logger.info(f"演员信息: {name}")
            actor_id = self.store.upsert_actor(name, note=f"source_url: {actor_url}", **meta)

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
            "list_source_id": src["id"],
        }

    def crawl_actor_info(self, actor_url: str) -> dict:
        """爬取演员详情页，提取基本信息。"""
        info = {}
        if not self._goto_with_retry(actor_url):
            logger.error(f"无法访问演员页（重试耗尽）: {actor_url}")
            return info
        try:
            # 姓名
            # JavDB 演员名元素常把 "N movie(s)" 计数一起带进 inner_text，
            # 需清洗：只取首行 + 去掉残留计数后缀，否则 upsert_actor 因名字
            # 不匹配而新建重复演员（作品挂错人）。
            try:
                name_el = self.page.locator(".actor-name, h2, .name, .title").first
                if name_el.count() > 0:
                    raw = (name_el.inner_text() or "").strip()
                    name = raw.split("\n")[0].strip()
                    name = re.sub(
                        r"\s*\d+\s*(movie\(s\)|movies|videos|works|部作品|部)\s*$",
                        "", name, flags=re.IGNORECASE,
                    ).strip()
                    if name:
                        info["name"] = name
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

    def crawl_actor_movies(self, actor_url: str, max_pages: int = 50, max_co_star: int = 0, solo_only: bool = False) -> list:
        """翻页爬取演员作品列表，返回详情页 URL 列表。

        max_co_star > 0 时开启共演人数限制：逐部访问作品详情页统计女演员数，
        超过上限的作品跳过（大共演/総集編不拉进库）。
        solo_only=True 只爬单体作品：javdb 演员页 t=s 过滤
        （第 1 页 ?sort_type=0&t=s，第 N 页 ?page=N&sort_type=0&t=s）。
        """
        all_urls = []
        page_num = 1
        base_url = actor_url.rstrip("/")

        def page_url(n: int) -> str:
            if solo_only:
                q = "sort_type=0&t=s"
                return f"{base_url}?{q}" if n == 1 else f"{base_url}?page={n}&{q}"
            return base_url if n == 1 else f"{base_url}?page={n}"

        while page_num <= max_pages:
            url = page_url(page_num)
            logger.info(f"爬取演员作品第 {page_num} 页: {url}")

            try:
                if not self._goto_with_retry(url):
                    logger.error(f"第 {page_num} 页导航失败（重试耗尽），停止翻页")
                    break

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
        all_urls = list(dict.fromkeys(all_urls))

        # 共演人数限制：逐部访问详情页统计女演员数，超过上限跳过
        # （单体作品模式下全部为 1 人，无需核对，直接跳过该环节省时间）
        if max_co_star > 0 and not solo_only and all_urls:
            logger.info(f"开始共演人数核对（上限 {max_co_star} 人），共 {len(all_urls)} 部")
            kept: list[str] = []
            skipped = 0
            for i, movie_url in enumerate(all_urls, start=1):
                try:
                    if not self._goto_with_retry(movie_url):
                        logger.warning(f"详情页访问失败，保留作品: {movie_url}")
                        kept.append(movie_url)
                        continue
                    pairs = self.scraper._extract_actors_with_gender()
                    count = sum(1 for _, g in pairs if g == "female")
                    if count and count > max_co_star:
                        skipped += 1
                        logger.info(f"跳过共演作品（{count} 人 > {max_co_star}）[{i}/{len(all_urls)}]: {movie_url}")
                        continue
                    kept.append(movie_url)
                    time.sleep(random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX))
                except Exception as e:
                    logger.warning(f"共演人数核对失败，保留作品: {movie_url} ({e})")
                    kept.append(movie_url)
            logger.info(f"共演人数核对完成: 保留 {len(kept)} 部，跳过 {skipped} 部")
            all_urls = kept
        return all_urls
