"""图片文件服务 —— 服务 scraper 下载的高清图（不在后端跑 Playwright）。

设计原则（PLAN）：scraper 抓图，后端服务图。
图片目录：{DATA_DIR}/images/{task_id}/poster.jpg | thumb_{N}.jpg | backdrop.jpg
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from config import get_settings

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
def list_thumbnails(task_id: int):
    """列出该任务的所有缩略图。"""
    d = _task_dir(task_id)
    if not d.exists():
        return {"thumbnails": [], "count": 0}
    thumbs = sorted(d.glob("thumb_*.jpg"))
    return {
        "thumbnails": [f"/api/images/thumb/{task_id}/{i}" for i in range(len(thumbs))],
        "count": len(thumbs),
    }


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
def hires_download(task_id: int):
    """触发高清图下载（Phase 1 占位）。实际抓取由 scraper 负责。"""
    return {"ok": True, "message": "高清图下载由 scraper 在抓取阶段完成，无需手动触发"}


@router.get("/hires/poster-index/{task_id}")
def hires_poster_index(task_id: int):
    """获取海报索引（Phase 1 占位：默认 0）。"""
    return {"poster_index": 0}


@router.post("/hires/set-poster/{task_id}/{index}")
def hires_set_poster(task_id: int, index: int):
    """设置海报索引（Phase 1 占位）。"""
    return {"ok": True, "message": "海报索引设置暂未实现"}


@router.post("/hires/queue/start")
def hires_queue_start(task_ids: list[int]):
    """启动串行下载队列（Phase 1 占位）。"""
    return {"ok": True, "message": "队列功能由 auto_crawl 调度替代"}


@router.get("/hires/queue/status")
def hires_queue_status():
    """队列状态（Phase 1 占位）。"""
    return {"running": False, "total": 0, "current": 0, "done": [], "failed": []}
