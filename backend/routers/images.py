"""图片文件服务 —— 服务 scraper 下载的高清图（不在后端跑 Playwright）。

设计原则（PLAN）：scraper 抓图，后端服务图。
图片目录：{DATA_DIR}/images/{task_id}/poster.jpg | thumb_{N}.jpg | backdrop.jpg
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from config import get_settings
from deps import DbSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/images", tags=["images"])


def _images_dir() -> Path:
    return Path(get_settings().IMAGES_DIR)


def _task_dir(task_id: int) -> Path:
    return _images_dir() / str(task_id)


@router.get("/poster/{task_id}")
def get_poster(task_id: int):
    """获取海报。"""
    d = _task_dir(task_id)
    for name in ("poster.jpg", "cover.jpg", "gallery-1.jpg"):
        p = d / name
        if p.exists():
            return FileResponse(str(p), media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="海报不存在")


@router.get("/backdrop/{task_id}")
def get_backdrop(task_id: int):
    """获取背景图。"""
    d = _task_dir(task_id)
    for name in ("backdrop.jpg", "gallery-2.jpg"):
        p = d / name
        if p.exists():
            return FileResponse(str(p), media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="背景图不存在")


@router.get("/thumbnails/{task_id}")
def list_thumbnails(task_id: int, db: DbSession):
    """列出该任务的所有缩略图。

    优先返回本地缓存的高清图；无缓存时 fallback 到 task.thumbnail_urls 远程 URL。
    """
    d = _task_dir(task_id)
    thumbs = sorted(d.glob("thumb_*.jpg")) if d.exists() else []
    if thumbs:
        return {
            "thumbnails": [f"/api/images/thumb/{task_id}/{i}" for i in range(len(thumbs))],
            "count": len(thumbs),
        }
    # 无本地缓存 → 从 DB 读远程缩略图 URL
    from models import Task
    task = db.get(Task, task_id)
    if task and task.thumbnail_urls:
        import json, re
        try:
            urls = json.loads(task.thumbnail_urls)
            if isinstance(urls, list) and urls:
                # 过滤 JavDB 封面小缩略图（_l_0 / _s_0 = 147x200，非真正预览图）
                cover_thumb_re = re.compile(r"/samples/[^/]+_[ls]_0\.jpg")
                filtered = [u for u in urls if not cover_thumb_re.search(u)] or urls
                return {"thumbnails": filtered, "count": len(filtered)}
        except Exception:
            pass
    return {"thumbnails": [], "count": 0}


@router.get("/thumb/{task_id}/{index}")
def get_thumb(task_id: int, index: int):
    """获取单张缩略图（按索引）。"""
    d = _task_dir(task_id)
    p = d / f"thumb_{index}.jpg"
    if not p.exists():
        raise HTTPException(status_code=404, detail="缩略图不存在")
    return FileResponse(str(p), media_type="image/jpeg")


# ── Phase 1 补端点：头像 + hires 简化适配 ──

@router.get("/avatar/{actor_id}")
def get_avatar(actor_id: int):
    """演员头像（查找本地缓存，未找到返回默认占位）。"""
    d = _images_dir() / "avatars"
    for ext in (".jpg", ".png", ".webp"):
        p = d / f"{actor_id}{ext}"
        if p.exists():
            return FileResponse(str(p), media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="头像不存在")


# hires 系列端点（Phase 1 简化版：复用 images 端点，路径映射）

@router.get("/hires/thumb-file/{task_id}/{index}")
def hires_thumb_file(task_id: int, index: int):
    """hires 兼容：重定向到 /images/thumb/..."""
    return get_thumb(task_id, index)


@router.get("/hires/poster-file/{task_id}")
def hires_poster_file(task_id: int):
    """hires 兼容：重定向到 /images/poster/..."""
    return get_poster(task_id)


@router.get("/hires/backdrop-file/{task_id}")
def hires_backdrop_file(task_id: int):
    """hires 兼容：重定向到 /images/backdrop/..."""
    return get_backdrop(task_id)


@router.get("/hires/has-local-thumbs/{task_id}")
def hires_has_local_thumbs(task_id: int):
    """检查是否有本地缩略图缓存。返回前端期望的 {has_local, count}。"""
    d = _task_dir(task_id)
    if not d.exists():
        return {"has_local": False, "count": 0}
    thumbs = list(d.glob("thumb_*.jpg"))
    count = len(thumbs)
    return {"has_local": count > 0, "count": count}


@router.post("/hires/download-hires/{task_id}")
def hires_download(task_id: int, db: DbSession):
    """下载高清预览图/封面/背景到本地缓存。

    数据源：task.thumbnail_urls（高清预览图，scraper 从 .tile-item href 提取）
           + task.poster_url（横版封面 covers/）
    下载到 {IMAGES_DIR}/{task_id}/：
      - thumb_{N}.jpg   预览图（thumbnail_urls）
      - poster.jpg      封面（poster_url）
      - backdrop.jpg    背景图（同 poster_url）
    走 HTTP_PROXY/HTTPS_PROXY 环境变量（与 scraper 一致）。
    """
    from models import Task

    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 解析预览图 URL 列表
    thumb_urls: list[str] = []
    if task.thumbnail_urls:
        try:
            arr = json.loads(task.thumbnail_urls)
            if isinstance(arr, list):
                thumb_urls = [u for u in arr if isinstance(u, str) and u.startswith("http")]
        except Exception:
            pass
    # 过滤 JavDB 封面小缩略图（_l_0 / _s_0 = 147x200，非真正预览图）
    # 实测规律：所有任务第一张 _l_0 都是封面缩略图，高清预览图从 _l_1 开始
    import re
    cover_thumb_re = re.compile(r"/samples/[^/]+_[ls]_0\.jpg")
    filtered = [u for u in thumb_urls if not cover_thumb_re.search(u)]
    if filtered:
        thumb_urls = filtered
    poster_url = task.poster_url

    total_found = len(thumb_urls) + (1 if poster_url else 0)
    if total_found == 0:
        raise HTTPException(status_code=400, detail="任务无图片 URL（需先爬取详情页）")

    # 准备目录
    d = _task_dir(task_id)
    d.mkdir(parents=True, exist_ok=True)

    # httpx 客户端（带 Referer 头 + 代理环境变量）
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or None
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://javdb.com/",
    }
    timeout = httpx.Timeout(30.0, connect=15.0)

    def _download(url: str, dest: Path) -> bool:
        try:
            with httpx.Client(proxy=proxy, timeout=timeout, follow_redirects=True, verify=False) as c:
                r = c.get(url, headers=headers)
                if r.status_code != 200 or len(r.content) < 500:
                    logger.warning(f"下载失败 {url}: status={r.status_code} bytes={len(r.content)}")
                    return False
                dest.write_bytes(r.content)
                return True
        except Exception as e:
            logger.warning(f"下载异常 {url}: {e}")
            return False

    # 下载预览图
    thumb_count = 0
    for i, url in enumerate(thumb_urls):
        dest = d / f"thumb_{i}.jpg"
        if dest.exists() and dest.stat().st_size > 500:
            thumb_count += 1  # 已缓存，跳过
            continue
        if _download(url, dest):
            thumb_count += 1

    # 下载封面 + 背景（都用 poster_url）
    cover_ok = False
    if poster_url:
        poster_dest = d / "poster.jpg"
        if not (poster_dest.exists() and poster_dest.stat().st_size > 500):
            cover_ok = _download(poster_url, poster_dest)
        else:
            cover_ok = True
        # 背景图复用封面
        backdrop_dest = d / "backdrop.jpg"
        if not backdrop_dest.exists() and poster_url:
            _download(poster_url, backdrop_dest)

    msg = f"已下载 {thumb_count} 张高清预览图" + ("，封面已缓存" if cover_ok else "")
    logger.info(f"task {task_id}: {msg} (found={total_found})")
    return {
        "ok": True,
        "message": msg,
        "downloaded": {"cover": cover_ok, "thumbnails": thumb_count, "total_found": total_found},
    }


@router.get("/hires/poster-index/{task_id}")
def hires_poster_index(task_id: int):
    """获取海报索引（Phase 1 占位：默认 0）。"""
    return {"poster_index": 0}


@router.post("/hires/set-poster/{task_id}/{index}")
def hires_set_poster(task_id: int, index: int):
    """把指定索引的本地预览图设为海报（复制 thumb_{index}.jpg → poster.jpg）。"""
    d = _task_dir(task_id)
    src = d / f"thumb_{index}.jpg"
    if not src.exists():
        raise HTTPException(status_code=404, detail=f"预览图 thumb_{index}.jpg 不存在")
    (d / "poster.jpg").write_bytes(src.read_bytes())
    return {"ok": True, "message": f"已将预览图 {index} 设为海报"}


@router.post("/hires/queue/start")
def hires_queue_start(task_ids: list[int]):
    """启动串行下载队列（Phase 1 占位）。"""
    return {"ok": True, "message": "队列功能由 auto_crawl 调度替代"}


@router.get("/hires/queue/status")
def hires_queue_status():
    """队列状态（Phase 1 占位）。"""
    return {"running": False, "total": 0, "current": 0, "done": [], "failed": []}
