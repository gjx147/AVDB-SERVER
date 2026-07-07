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
