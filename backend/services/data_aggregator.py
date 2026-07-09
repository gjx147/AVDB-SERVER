"""多源元数据聚合 —— 从 JavLibrary 等外部源抓取补充元数据合并到 tasks。

设计参考 JavdBviewed DataAggregator：
- 多源并行（asyncio.gather）+ 容错扇出（单源失败静默忽略）
- 标准化为统一 VideoMetadata 结构
- upsert 到 tasks 表（只填空字段，不覆盖已有数据）

数据源：
- JavLibrary：评分/演员/厂牌/发行日期（DOM 解析）
- 后续可扩展 blogJav（封面）/dmm 等
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from bs4 import BeautifulSoup  # type: ignore[import-untyped]  # 后续加进 requirements
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Task

logger = logging.getLogger("avdb.aggregator")

# 模块级共享 httpx 客户端（Phase 4：连接复用）
_http_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=15, follow_redirects=True)
    return _http_client

# 番号标准化：SSIS-001 / ssis001 / SSIS001 -> SSIS-001
_CODE_RE = re.compile(r"^([A-Za-z]{2,6})[-_]?(\d{2,5})$")


def normalize_code(raw: str) -> str:
    """标准化番号。"""
    m = _CODE_RE.match(raw.strip())
    if not m:
        return raw.strip().upper()
    return f"{m.group(1).upper()}-{m.group(2)}"


@dataclass
class VideoMetadata:
    """标准化的影片元数据（多源合并结果）。"""

    video_code: str
    title: str | None = None
    rating: float | None = None
    actors: list[str] = field(default_factory=list)
    maker: str | None = None
    studio: str | None = None
    release_date: str | None = None
    genres: list[str] = field(default_factory=list)
    cover_url: str | None = None
    source: str = ""  # 数据来源标记


async def _fetch_javlibrary(code: str) -> VideoMetadata | None:
    """从 JavLibrary 抓取元数据。

    JavLibrary 域名经常变，这里用可配置的镜像 + 搜索定位。
    """
    base = "https://www.javlibrary.com"
    search_url = f"{base}/cn/vl_searchbyid.php?keyword={code}"
    try:
        client = _get_client()
        resp = await client.get(search_url)
        if resp.status_code != 200:
            return None
        html = resp.text
    except Exception as e:
        logger.debug(f"JavLibrary 抓取失败({code}): {e}")
        return None

    soup = BeautifulSoup(html, "html.parser")
    # 详情页链接
    video_link = soup.select_one("a.videothumbimg, a[href*='?v=']")
    if not video_link:
        return None
    href = video_link.get("href", "")
    if not href.startswith("http"):
        href = base + href
    try:
        client = _get_client()
        detail = await client.get(href)
        if detail.status_code != 200:
            return None
        html = detail.text
    except Exception:
        return None

    soup = BeautifulSoup(html, "html.parser")
    meta = VideoMetadata(video_code=code, source="javlibrary")

    # 标题
    title_el = soup.select_one("#video_title h3, #video_title")
    if title_el:
        meta.title = title_el.get_text(strip=True)

    # 评分
    score_el = soup.select_one("#video_review .score, .score")
    if score_el:
        try:
            meta.rating = float(score_el.get_text(strip=True))
        except ValueError:
            pass

    # 演员
    for a in soup.select("#video_actor a, a[href*='vl_star.php']"):
        name = a.get_text(strip=True)
        if name:
            meta.actors.append(name)

    # 元数据面板
    for row in soup.select("#video_info .item, #video_jacket_info .info"):
        label = row.select_one(".header, .label")
        if not label:
            continue
        txt = label.get_text(strip=True).rstrip(":")
        val_el = row.select_one("a, .value") or label.find_next_sibling()
        val = val_el.get_text(strip=True) if val_el else None
        if "製作商" in txt or "厂商" in txt or "Studio" in txt:
            meta.studio = val
        elif "發行商" in txt or "发行" in txt or "Maker" in txt:
            meta.maker = val
        elif "發行日期" in txt or "发行日期" in txt or "Release" in txt:
            meta.release_date = val

    # 封面
    img = soup.select_one("#video_jacket_img img, #video_jacket img")
    if img:
        meta.cover_url = img.get("src") or img.get("data-src")

    # 类型标签
    for a in soup.select("#video_genres a, #video_genre a"):
        g = a.get_text(strip=True)
        if g:
            meta.genres.append(g)

    return meta


async def aggregate(code: str) -> VideoMetadata:
    """聚合多源数据，返回合并后的元数据。任一源失败不影响其他。"""
    norm = normalize_code(code)
    results = await asyncio.gather(
        _fetch_javlibrary(norm),
        # 后续扩展: _fetch_blogjav(norm), _fetch_dmm(norm)
        return_exceptions=True,
    )
    # 合并：JavLibrary 优先（目前唯一源）
    merged = VideoMetadata(video_code=norm)
    for r in results:
        if isinstance(r, VideoMetadata):
            for f in ("title", "rating", "maker", "studio", "release_date", "cover_url"):
                if not getattr(merged, f) and getattr(r, f):
                    setattr(merged, f, getattr(r, f))
            if not merged.actors:
                merged.actors = r.actors
            if not merged.genres:
                merged.genres = r.genres
            if not merged.source:
                merged.source = r.source
    return merged


def apply_to_task(db: Session, task: Task, meta: VideoMetadata, *, overwrite: bool = False) -> bool:
    """把聚合元数据写入 task（默认只填空字段，overwrite=True 全覆盖）。返回是否有更新。"""
    changed = False
    fields_map = {
        "title": meta.title,
        "rating": meta.rating,
        "maker": meta.maker,
        "label": meta.studio,  # studio 映射到 label
        "release_date": meta.release_date,
        "poster_url": meta.cover_url,
    }
    for db_field, val in fields_map.items():
        if val is None:
            continue
        cur = getattr(task, db_field, None)
        if overwrite or cur is None or cur == "":
            if cur != val:
                setattr(task, db_field, val)
                changed = True
    # 演员（合并去重）
    if meta.actors:
        existing = set(filter(None, (task.actors or "").split(",")))
        new_actors = existing | set(meta.actors)
        joined = ",".join(sorted(new_actors))
        if joined != (task.actors or ""):
            task.actors = joined
            changed = True
    # 标签
    if meta.genres:
        existing_tags = set(filter(None, (task.tags or "").split(",")))
        new_tags = existing_tags | set(meta.genres)
        joined = ",".join(sorted(new_tags))
        if joined != (task.tags or ""):
            task.tags = joined
            changed = True
    if changed:
        task.updated_at = datetime.utcnow()
    return changed


async def enrich_task(task_id: int, *, overwrite: bool = False) -> dict:
    """对单个任务执行多源聚合 + 写库。返回更新摘要。"""
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if not task or not task.video_code:
            return {"ok": False, "message": "任务不存在或无番号"}
        meta = await aggregate(task.video_code)
        changed = apply_to_task(db, task, meta, overwrite=overwrite)
        if changed:
            db.commit()
        return {
            "ok": True,
            "task_id": task_id,
            "code": task.video_code,
            "source": meta.source,
            "changed": changed,
            "title": meta.title,
            "rating": meta.rating,
            "actors": meta.actors[:5],
        }
    finally:
        db.close()
