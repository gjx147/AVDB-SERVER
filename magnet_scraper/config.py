"""爬虫配置 —— 从环境变量 + backend settings 表读取（不硬编码 javdb_url）。

scraper 作为独立子进程，通过环境变量获取基础配置；
JAVDB_URL 优先读环境变量，回退到默认值（运行时可由 backend 写 settings 表覆盖）。
"""

from __future__ import annotations

import os
from pathlib import Path

# 目标网站（可由环境变量 JAVDB_URL 覆盖，默认 javdb.com）
BASE_URL = os.environ.get("JAVDB_URL", "https://javdb.com").rstrip("/") or "https://javdb.com"

# 列表页配置
LIST_PATH = "/video_codes/SDMM"
LIST_PARAMS = "f=download"

# 爬取参数
MAX_PAGES = int(os.environ.get("SCRAPER_MAX_PAGES", "100"))
REQUEST_DELAY_MIN = int(os.environ.get("SCRAPER_REQUEST_DELAY_MIN", "2"))
REQUEST_DELAY_MAX = int(os.environ.get("SCRAPER_REQUEST_DELAY_MAX", "5"))
DETAIL_DELAY_MIN = int(os.environ.get("SCRAPER_DETAIL_DELAY_MIN", "20"))
DETAIL_DELAY_MAX = int(os.environ.get("SCRAPER_DETAIL_DELAY_MAX", "40"))


def get_settings_override():
    """从 DB settings 表读取运行时覆盖值（crawl_delay_min/max）。

    scraper 启动时调一次，覆盖 config 里的 env 默认值。
    """
    global REQUEST_DELAY_MIN, REQUEST_DELAY_MAX
    try:
        import sqlite3
        from pathlib import Path
        db_path = DATA_DIR / "javdb.db"
        if not db_path.exists():
            return
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            for key, attr in [("crawl_delay_min", "REQUEST_DELAY_MIN"),
                              ("crawl_delay_max", "REQUEST_DELAY_MAX")]:
                row = conn.execute("SELECT value FROM settings WHERE key=? LIMIT 1", (key,)).fetchone()
                if row and row[0]:
                    val = int(row[0])
                    if val > 0:
                        globals()[attr] = val
        finally:
            conn.close()
    except Exception:
        pass
MAX_RETRIES = 3
PAGE_TIMEOUT = 30000

# 磁力优先级后缀（无码有字 > 有字 > 无码）
PREFERRED_SUFFIXES = ['-UC', '-C', '-U']

# 浏览器
HEADLESS = True
SLOW_MO = 100

# 输出目录（文件模式用）
OUTPUT_DIR = Path(__file__).parent / "output"
LIST_CODE = LIST_PATH.rstrip("/").split("/")[-1] or "magnets"
OUTPUT_FILE = OUTPUT_DIR / "magnets.json"
MAGNETS_LIST_FILE = OUTPUT_DIR / f"{LIST_CODE}.txt"
COOKIE_FILE = OUTPUT_DIR / "cookies.json"
VISITED_URLS_FILE = OUTPUT_DIR / "visited_urls.txt"
PENDING_URLS_FILE = OUTPUT_DIR / "pending_urls.txt"
COMPLETED_DIR = OUTPUT_DIR / "已完成的番号"

# 数据库（与 backend 共用，项目根 data/javdb.db）
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "javdb.db"

# 爬取状态文件（供 backend/前端读取进度）
CRAWL_STATUS_FILE = DATA_DIR / "crawl_status.json"

# 后端服务地址（爬虫启动时注册状态；空则不上报）
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000").strip() or None
