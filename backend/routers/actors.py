"""演员库路由 —— 档案/列表筛选/关注/拉黑/详情(含关联作品)。"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select

from deps import CurrentUser, DbSession, Pagination
from models import Actor, Subscription, Task, actor_movies
from schemas import ActorDetailOut, ActorListResponse, ActorOut, ActorProfileUpdate

router = APIRouter(prefix="/api/actors", tags=["actors"])
logger = logging.getLogger("avdb.actors")


@router.get("", response_model=ActorListResponse)
def list_actors(
    db: DbSession,
    _user: CurrentUser,
    pagination: Pagination,
    q: str | None = Query(None, description="按名字搜索"),
    followed: bool | None = Query(None, description="只看关注的"),
    blacklisted: bool | None = Query(None, description="只看拉黑的"),
    with_avatar: bool | None = Query(None, description="只看有头像的（已爬详情）"),
):
    """演员列表，支持名字搜索 + 关注/拉黑/头像筛选 + 分页。"""
    stmt = select(Actor)
    count_stmt = select(func.count(Actor.id))
    # 「有 actor 订阅」子查询（关注现已 = actor 订阅）
    has_sub_sq = select(Subscription.actor_id).where(Subscription.sub_type == "actor")
    if q:
        like = f"%{q}%"
        cond = or_(Actor.name.like(like), Actor.name_en.like(like))
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    if followed is True:
        stmt = stmt.where(Actor.id.in_(has_sub_sq))
        count_stmt = count_stmt.where(Actor.id.in_(has_sub_sq))
    elif followed is False:
        stmt = stmt.where(~Actor.id.in_(has_sub_sq))
        count_stmt = count_stmt.where(~Actor.id.in_(has_sub_sq))
    if blacklisted is not None:
        stmt = stmt.where(Actor.is_blacklisted == blacklisted)
        count_stmt = count_stmt.where(Actor.is_blacklisted == blacklisted)
    if with_avatar is True:
        # 只显示有头像的演员（avatar_url 非空）
        stmt = stmt.where(Actor.avatar_url.isnot(None), Actor.avatar_url != "")
        count_stmt = count_stmt.where(Actor.avatar_url.isnot(None), Actor.avatar_url != "")

    offset, limit = pagination
    total = db.execute(count_stmt).scalar_one()
    items = (
        db.execute(stmt.order_by(Actor.id.in_(has_sub_sq).desc(), Actor.id.desc()).offset(offset).limit(limit))
        .scalars()
        .all()
    )
    return ActorListResponse(total=total, page=offset // limit + 1, page_size=limit, items=items)


@router.get("/{actor_id}", response_model=ActorDetailOut)
def get_actor(actor_id: int, db: DbSession, _user: CurrentUser):
    """演员详情，含关联作品 ID 列表。"""
    actor = db.get(Actor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    movie_ids = [
        r[0]
        for r in db.execute(
            select(actor_movies.c.task_id).where(actor_movies.c.actor_id == actor_id)
        ).all()
    ]
    return ActorDetailOut(
        **{c.name: getattr(actor, c.name) for c in actor.__table__.columns},
        movie_ids=movie_ids,
    )


@router.post("/{actor_id}/follow")
def follow(actor_id: int, db: DbSession, _user: CurrentUser):
    """关注演员 = 创建 actor 订阅（auto_add=false：定时检测+通知，不入库）。已存在则 no-op。"""
    actor = db.get(Actor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    existing = db.execute(
        select(Subscription).where(
            Subscription.sub_type == "actor", Subscription.actor_id == actor_id
        )
    ).scalar_one_or_none()
    if not existing:
        db.add(Subscription(
            name=actor.name, sub_type="actor", actor_id=actor_id,
            auto_add=False, enabled=True, check_interval_hours=6,
        ))
        db.commit()
    return {"ok": True, "actor_id": actor_id, "subscribed": True}


@router.post("/{actor_id}/unfollow")
def unfollow(actor_id: int, db: DbSession, _user: CurrentUser):
    """取消关注 = 删除该 actor 订阅。"""
    sub = db.execute(
        select(Subscription).where(
            Subscription.sub_type == "actor", Subscription.actor_id == actor_id
        )
    ).scalar_one_or_none()
    if sub:
        db.delete(sub)
        db.commit()
    return {"ok": True, "actor_id": actor_id, "subscribed": False}


@router.post("/{actor_id}/auto-add")
def toggle_auto_add(actor_id: int, db: DbSession, _user: CurrentUser):
    """切换该演员订阅的 auto_add（自动入库+下载）。需先关注（存在 actor 订阅）。"""
    sub = db.execute(
        select(Subscription).where(
            Subscription.sub_type == "actor", Subscription.actor_id == actor_id
        )
    ).scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=400, detail="请先关注该演员")
    sub.auto_add = not sub.auto_add
    db.commit()
    return {"ok": True, "actor_id": actor_id, "auto_add": sub.auto_add}


@router.post("/{actor_id}/blacklist")
def toggle_blacklist(actor_id: int, db: DbSession, _user: CurrentUser):
    """切换拉黑状态。"""
    actor = db.get(Actor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    actor.is_blacklisted = not actor.is_blacklisted
    db.commit()
    return {"ok": True, "is_blacklisted": actor.is_blacklisted}


@router.delete("/{actor_id}")
def delete_actor(actor_id: int, db: DbSession, _user: CurrentUser):
    """删除演员（actor_movies 由 ON DELETE CASCADE 自动清理）。"""
    actor = db.get(Actor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    db.delete(actor)
    db.commit()
    return {"ok": True, "message": "已删除"}


@router.patch("/{actor_id}")
def update_actor_profile(actor_id: int, body: ActorProfileUpdate, db: DbSession, _user: CurrentUser):
    """手动编辑演员资料（intro/bio/timeline/profile_locked，未传字段不更新）。"""
    actor = db.get(Actor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        if hasattr(actor, k):
            if isinstance(v, str):
                setattr(actor, k, v.strip() or None)
            else:
                setattr(actor, k, v)
    db.commit()
    logger.info("演员资料已手动更新: %s (id=%d) %s", actor.name, actor_id, ",".join(data))
    return {"ok": True}


@router.get("/{actor_id}/movies")
def actor_movies_list(
    actor_id: int, db: DbSession, _user: CurrentUser,
    page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100),
    sort: str = Query("added", description="排序：added=加入日期 / release=发行日期"),
    in_library: bool | None = Query(None, description="按 Emby 在库状态筛选"),
):
    """演员的关联作品列表（分页，只含有磁力链接的作品）。"""
    actor = db.get(Actor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    # 只展示有磁力链接的作品：爬取不到磁力的（pending/failed/空磁力）不在详情页显示
    conds = [
        actor_movies.c.actor_id == actor_id,
        Task.best_magnet.isnot(None),
        Task.best_magnet != "",
    ]
    if in_library is not None:
        conds.append(Task.media_in_library == in_library)
    total = db.execute(
        select(func.count(Task.id))
        .select_from(Task)
        .join(actor_movies, actor_movies.c.task_id == Task.id)
        .where(*conds)
    ).scalar_one()
    # 排序：release=发行日期 / rating=评分 / 默认 added=加入日期；id 作 tiebreaker 保证分页稳定
    if sort == "release":
        order = (Task.release_date.desc(), Task.id.desc())
    elif sort == "rating":
        order = (Task.rating.desc(), Task.id.desc())
    else:
        order = (Task.created_at.desc(), Task.id.desc())
    tasks = db.execute(
        select(Task)
        .join(actor_movies, actor_movies.c.task_id == Task.id)
        .where(*conds)
        .order_by(*order)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()
    return {
        "items": [{
            "id": t.id, "video_code": t.video_code, "title": t.title, "status": t.status,
            "poster_url": t.poster_url, "thumbnail_urls": t.thumbnail_urls,
            "rating": t.rating, "is_favorite": int(t.is_favorite),
            "media_in_library": t.media_in_library,
        } for t in tasks],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/{actor_id}/crawl-works")
def crawl_actor_works(actor_id: int, db: DbSession, _user: CurrentUser):
    """一键补齐演员作品：读 actor.source_url → 触发 crawl-actor 子进程。"""
    actor = db.get(Actor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    url = actor.source_url
    # fallback: 旧数据 source_url 为空时从 note 解析
    if not url and actor.note and actor.note.startswith("source_url: "):
        url = actor.note[len("source_url: "):]
    if not url:
        raise HTTPException(status_code=400, detail="该演员无 JavDB URL，需先在演员库通过 URL 添加")
    # 复用 crawl 模块的子进程启动逻辑（含全局进程锁）
    # 传入 actor_id：让 scraper 按 id 关联作品，避免名字匹配建重复演员
    from routers.crawl import start_actor_crawl
    return start_actor_crawl(url, actor_id=actor.id)


# ── 双源资料聚合：手动重试 + 队列状态（自动抓取由 actor_profile_sync 定时任务完成）──

@router.get("/{actor_id}/avatar-options")
def avatar_options(actor_id: int, db: DbSession, _user: CurrentUser):
    """演员头像手动更换候选：laoshi（高清）/ minnano-av / JavDB 三选一。

    laoshi 走本地磁盘缓存即时返回；minnano 实时搜索（可能稍慢）；
    JavDB 从 source_url 派生头像地址（c0.jdbstatic.com/avatars/xx/Hash.jpg），零网络。
    """
    actor = db.get(Actor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    from services.actor_profile import fetch_laoshi, fetch_minnano

    opts: list[dict] = []

    def add(key: str, label: str, url: str | None):
        if url and all(o["url"] != url for o in opts):
            opts.append({"key": key, "label": label, "url": url})

    # laoshi：磁盘缓存（零网络，秒回）
    la = fetch_laoshi(actor.name)
    if not la and actor.name_en:
        la = fetch_laoshi(actor.name_en)
    add("laoshi", "老师图鉴 · 高清", (la or {}).get("avatar_url"))
    # minnano-av：实时搜索（带重试/直连兜底）
    mn = fetch_minnano(actor.name)
    if not mn and actor.name_en:
        mn = fetch_minnano(actor.name_en)
    add("minnano", "minnano-av", mn.get("avatar_url"))
    # JavDB：由 source_url 的 /actors/Hash 派生（与封面 c0.jdbstatic 同款路径）
    m = re.search(r"/actors/([A-Za-z0-9]+)", actor.source_url or "")
    if m:
        h = m.group(1)
        add("javdb", "JavDB", f"https://c0.jdbstatic.com/avatars/{h[:2].lower()}/{h}.jpg")
    return {"current": actor.avatar_url, "options": opts}


@router.post("/{actor_id}/refresh-profile")
def refresh_actor_profile(actor_id: int, db: DbSession, _user: CurrentUser):
    """手动触发三源资料抓取（minnano-av + WAPdB + laoshi），成功后重置队列标记。

    profile_locked 时跳过全部资料字段（防误刷新覆盖手动编辑内容），
    仅重置队列标记；未锁定时照常写入全部抓取字段。
    """
    actor = db.get(Actor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    from services.actor_profile import fetch_profile
    result = fetch_profile(actor.name, actor.name_en)
    if not result.get("ok"):
        return {"ok": False, "source": None, "message": result.get("message", "minnano、WAPdB 与老师图鉴均未查询到")}
    fields = result.get("fields") or {}
    locked_skipped: list[str] = []
    if actor.profile_locked:
        locked_skipped = [k for k in fields if hasattr(actor, k)]
    else:
        for k, v in fields.items():
            if hasattr(actor, k) and v:
                setattr(actor, k, v)
    actor.profile_fetched = True
    actor.profile_fetch_failed = False
    db.commit()
    return {"ok": True, "source": result.get("source"), "fields": result.get("fields"),
            "locked_skipped": locked_skipped}


@router.get("/profile-queue/status")
def profile_queue_status(db: DbSession, _user: CurrentUser):
    """资料自动抓取队列状态（未抓/已抓/失败计数）。"""
    from sqlalchemy import func as sa_func
    pending = db.execute(sa_func.count(Actor.id).filter(Actor.profile_fetched.is_(False), Actor.profile_fetch_failed.is_(False))).scalar_one()
    done = db.execute(sa_func.count(Actor.id).filter(Actor.profile_fetched.is_(True))).scalar_one()
    failed = db.execute(sa_func.count(Actor.id).filter(Actor.profile_fetch_failed.is_(True))).scalar_one()
    return {"pending": pending, "fetched": done, "failed": failed}
