"""Pydantic 请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


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
    list_code: str = Field(max_length=50)
    list_path: Optional[str] = Field(default=None, max_length=200)
    list_params: str = Field(default="f=download", max_length=100)
    max_pages: int = Field(default=100, ge=1, le=10000)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    list_source_id: int
    url: str
    status: str
    retry_count: int
    best_magnet: Optional[str] = None
    magnets_json: Optional[str] = None
    video_code: Optional[str] = None
    error_message: Optional[str] = None
    title: Optional[str] = None
    poster_url: Optional[str] = None
    thumbnail_urls: Optional[str] = None
    synopsis: Optional[str] = None
    description: Optional[str] = None
    actors: Optional[str] = None
    tags: Optional[str] = None
    release_date: Optional[str] = None
    duration: Optional[str] = None
    director: Optional[str] = None
    maker: Optional[str] = None
    label: Optional[str] = None
    series: Optional[str] = None
    rating: Optional[float] = None
    file_size: Optional[str] = None
    is_favorite: bool = False
    favorite_at: Optional[datetime] = None
    note: Optional[str] = None
    view_status: Optional[str] = None
    ai_title_translated: Optional[str] = None
    ai_tags: Optional[str] = None
    media_in_library: Optional[bool] = None
    download_status: Optional[str] = None
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


class RankingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    rank_type: str
    rank_date: str
    rank_position: int
    video_code: Optional[str] = None
    title: Optional[str] = None
    cover_url: Optional[str] = None
    score: Optional[float] = None
    views: int = 0
    detail_url: Optional[str] = None
    task_id: Optional[int] = None
    is_in_library: bool = False
    created_at: Optional[datetime] = None
    # join tasks 表的真实数据（extract 后填充）
    task_video_code: Optional[str] = None
    task_title: Optional[str] = None
    task_poster_url: Optional[str] = None
    task_thumbnail_urls: Optional[str] = None
    task_status: Optional[str] = None


class BatchAddTasksRequest(BaseModel):
    ranking_ids: list[int]


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    sub_type: str
    rank_type: Optional[str] = None
    actor_id: Optional[int] = None
    filters_json: Optional[str] = None
    auto_add: bool = True
    target_list_source_id: Optional[int] = None
    enabled: bool = True
    check_interval_hours: int = 6
    last_checked_at: Optional[datetime] = None
    last_result: Optional[str] = None


class SubscriptionCreate(BaseModel):
    name: str
    sub_type: str  # ranking/actor/composite
    rank_type: Optional[str] = None
    actor_id: Optional[int] = None
    filters_json: Optional[str] = None
    auto_add: bool = True
    target_list_source_id: Optional[int] = None
    enabled: bool = True
    check_interval_hours: int = 6
