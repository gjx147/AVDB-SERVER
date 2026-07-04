"""Pydantic 请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ListSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    list_code: str
    list_path: str
    list_params: str
    max_pages: int
    last_scanned_page: int
    last_scanned_at: Optional[datetime] = None


class ListSourceCreate(BaseModel):
    list_code: str
    list_path: Optional[str] = None
    list_params: str = "f=download"
    max_pages: int = 100


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    list_source_id: int
    url: str
    status: str
    retry_count: int
    video_code: Optional[str] = None
    best_magnet: Optional[str] = None
    title: Optional[str] = None
    poster_url: Optional[str] = None
    actors: Optional[str] = None
    tags: Optional[str] = None
    rating: Optional[float] = None
    view_status: Optional[str] = None
    is_favorite: bool = False
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[TaskOut]


class CrawlStatusOut(BaseModel):
    running: bool
    list_code: Optional[str] = None
    crawl_type: Optional[str] = None
    phase: Optional[str] = None
    current_index: Optional[int] = None
    total: Optional[int] = None
    current_video_code: Optional[str] = None
    message: Optional[str] = None


class MessageResponse(BaseModel):
    ok: bool
    message: str
    data: Optional[dict] = None


class ActorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    name_en: Optional[str] = None
    avatar_url: Optional[str] = None
    avatar_local: Optional[str] = None
    gender: Optional[str] = None
    birth_date: Optional[str] = None
    height: Optional[str] = None
    cup: Optional[str] = None
    movie_count: Optional[int] = None
    is_followed: bool = False
    is_blacklisted: bool = False


class ActorListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ActorOut]


class ActorDetailOut(ActorOut):
    measurements: Optional[str] = None
    debut_date: Optional[str] = None
    note: Optional[str] = None
    movie_ids: list[int] = []
