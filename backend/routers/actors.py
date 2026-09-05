"""演员库路由 —— 档案/列表筛选/关注/拉黑/详情(含关联作品)。"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from deps import CurrentUser, DbSession, Pagination
from models import Actor, NewRelease, Subscription, Task, actor_movies
from schemas import ActorDetailOut, ActorListResponse, ActorOut, ActorProfileUpdate

router = APIRouter(prefix="/api/actors", tags=["actors"])
logger = logging.getLogger("avdb.actors")


def _avatar_dir() -> Path:
    """本地上传头像目录（项目根 data/images/avatars/，Docker 持久卷 /app/data）。"""
    return Path(__file__).resolve().parents[2] / "data" / "images" / "avatars"


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
        cond = or_(Actor.name.like(like), Actor.name_en.like(like), Actor.alias.like(like))
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
    # 兼容旧数据：source_url 为空但 note 存了 "source_url: <url>" 时归一化返回
    # （历史版本把 JavDB 链接存在 note 字段；crawl_actor_works 已有同款 fallback，
    # 详情接口补齐后前端「补齐作品/补齐单体作品」按钮恢复可点。
    # 根治方案：scripts/migrate_actor_source_url.py 一次性迁移到 source_url 列）
    _actor_data = {c.name: getattr(actor, c.name) for c in actor.__table__.columns}
    if not _actor_data.get("source_url") and str(_actor_data.get("note") or "").startswith("source_url: "):
        _actor_data["source_url"] = str(_actor_data["note"])[len("source_url: "):].strip()
    return ActorDetailOut(
        **_actor_data,
        movie_ids=movie_ids,
    )


@router.post("/{actor_id}/follow")
async def follow(actor_id: int, db: DbSession, _user: CurrentUser):
    """关注演员 = 创建 actor 订阅（auto_add=True：新作自动入库+下载，无需手动）。

    新建订阅后立即在后台触发一次新作巡检：爬取该演员 javdb 作品入库 + 新作检测。
    已存在则 no-op（不重复触发）。
    """
    actor = db.get(Actor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    existing = db.execute(
        select(Subscription).where(
            Subscription.sub_type == "actor", Subscription.actor_id == actor_id
        )
    ).scalar_one_or_none()
    immediate = False
    if not existing:
        sub = Subscription(
            name=actor.name, sub_type="actor", actor_id=actor_id,
            auto_add=True, enabled=True, check_interval_hours=6,
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        immediate = True
    if immediate:
        import asyncio
        from services.new_works_monitor import check_actor_new_works
        logger.info("关注演员 %s：后台触发首次巡检（自动爬取 javdb 作品，auto_add=True）", actor.name)
        task = asyncio.create_task(check_actor_new_works(actor_id, subscription_id=sub.id, auto_add=True))

        def _done(t):
            try:
                t.result()
            except Exception as e:
                logger.warning("关注后首次巡检异常 %s: %s", actor.name, e)

        task.add_done_callback(_done)
    return {"ok": True, "actor_id": actor_id, "subscribed": True, "auto_add": True, "immediate_check": immediate}


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
    q: str = Query("", max_length=100, description="关键字搜索：番号/标题模糊匹配"),
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
    if q.strip():
        kw = q.strip().replace("%", r"\%").replace("_", r"\_")
        pat = f"%{kw}%"
        conds.append(or_(Task.video_code.ilike(pat, escape="\\"), Task.title.ilike(pat, escape="\\")))
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
            "tags": t.tags,
        } for t in tasks],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


class CrawlWorksRequest(BaseModel):
    """补齐作品请求：最大共演人数限制＋作品过滤模式。

    video_filter: solo=单体(t=s) / magnet=含磁链(t=d) / subtitle=含字幕(t=c) / none=不过滤（需 javdb 登录）
    exclude_vr: 排除 VR 作品（演员页 VR 标签集合差；需登录）
    """
    max_co_star: int | None = None
    solo_only: bool = False
    video_filter: str = "none"
    exclude_vr: bool = False


@router.post("/{actor_id}/crawl-works")
def crawl_actor_works(actor_id: int, body: CrawlWorksRequest | None, db: DbSession, _user: CurrentUser):
    """一键补齐演员作品：读 actor.source_url → 触发 crawl-actor 子进程。"""
    actor = db.get(Actor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    url = actor.source_url
    # fallback: 旧数据 source_url 为空时从 note 解析
    if not url and actor.note and actor.note.startswith("source_url: "):
        url = actor.note[len("source_url: "):]
    max_co_star = (body.max_co_star if body else None)
    solo_only = bool(body.solo_only) if body else False
    video_filter = (getattr(body, "video_filter", "none") or "none") if body else "none"
    exclude_vr = bool(getattr(body, "exclude_vr", False)) if body else False
    if video_filter != "none":
        _parts = [p.strip() for p in video_filter.split(",") if p.strip() in ("solo", "magnet", "subtitle")]
        video_filter = ",".join(_parts) if _parts else "none"
    # 复用 crawl 模块的子进程启动逻辑（含全局进程锁）
    # 传入 actor_id：让 scraper 按 id 关联作品，避免名字匹配建重复演员
    from routers.crawl import start_actor_crawl
    return start_actor_crawl(url, actor_id=actor.id, max_co_star=max_co_star,
                             solo_only=solo_only, actor_name=actor.name,
                             video_filter=video_filter, exclude_vr=exclude_vr)


# ── 双源资料聚合：手动重试 + 队列状态（自动抓取由 actor_profile_sync 定时任务完成）──

@router.post("/{actor_id}/avatar-upload")
async def avatar_upload(actor_id: int, db: DbSession, _user: CurrentUser,
                        file: UploadFile = File(...)):
    """手动上传头像：存 data/images/avatars/，avatar_url 指向本地鉴权端点。"""
    import os
    from config import get_settings
    ext = (os.path.splitext(file.filename or "")[1] or ".jpg").lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        raise HTTPException(status_code=400, detail="仅支持 jpg / png / webp / gif 图片")
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片过大（>5MB）")
    actor = db.get(Actor, actor_id)
    if not actor:
        raise HTTPException(status_code=404, detail="演员不存在")
    # 项目根/data = Docker 挂载卷 /app/data（持久）
    d = _avatar_dir()
    d.mkdir(parents=True, exist_ok=True)
    for old_f in d.glob(f"actor-{actor_id}.*"):
        try:
            old_f.unlink()
        except Exception:
            pass
    f = d / f"actor-{actor_id}{ext}"
    f.write_bytes(raw)
    url = f"/api/images/avatars/actor-{actor_id}{ext}?v={int(datetime.utcnow().timestamp())}"
    actor.avatar_url = url
    db.commit()
    return {"ok": True, "avatar_url": url}


class ActorMergeIn(BaseModel):
    keep_id: int
    source_ids: list[int]


@router.post("/merge")
def merge_actors(payload: ActorMergeIn, db: DbSession, _user: CurrentUser):
    """合并重复演员档案：把 source_ids 的作品关联/新作发现/订阅迁移到 keep_id，旧名记入 alias，删除被合并档案。

    语义要点：
    - actor_movies 无唯一约束：先删 keep 侧已有的重复关联行，再改写剩余行；
    - new_releases.actor_id 级联删除：删档案前必须迁移，video_code 重复的丢弃；
    - 订阅受 (sub_type, actor_id) 唯一约束：keep 无订阅时转移，都有则删被合并侧并 OR 合并 auto_add；
    - 不迁移 source_url：保留主档案自己的「名字-页面」配对，避免巡检名字安全闸 actor_name_mismatch；
    - 旧名写入 alias（/ 分隔、去重、截 200）：配合 store.py 爬虫判重与 cast/搜索的 alias 匹配生效。
    """
    if payload.keep_id in payload.source_ids:
        raise HTTPException(status_code=400, detail="保留档案不能同时出现在合并列表里")
    if not payload.source_ids:
        raise HTTPException(status_code=400, detail="请选择要合并的重复档案")
    if len(set(payload.source_ids)) != len(payload.source_ids):
        raise HTTPException(status_code=400, detail="合并列表里有重复档案")
    keep = db.get(Actor, payload.keep_id)
    if not keep:
        raise HTTPException(status_code=404, detail="保留档案不存在")
    sources = []
    for sid in payload.source_ids:
        a = db.get(Actor, sid)
        if not a:
            raise HTTPException(status_code=404, detail=f"待合并档案 {sid} 不存在")
        sources.append(a)

    avatar_dir = _avatar_dir()

    def _local_avatar(actor_id: int) -> Path | None:
        for p in sorted(avatar_dir.glob(f"actor-{actor_id}.*")):
            return p
        return None

    keep_local = _local_avatar(keep.id)
    moved_movies = 0
    moved_subs = 0
    aliases_added: list[str] = []
    avatar = "kept"

    for src in sources:
        # 1) 作品关联迁移（先去重再改写，避免产生 (actor_id, task_id) 双行）
        db.execute(actor_movies.delete().where(
            actor_movies.c.actor_id == src.id,
            actor_movies.c.task_id.in_(select(actor_movies.c.task_id).where(actor_movies.c.actor_id == keep.id)),
        ))
        res = db.execute(actor_movies.update().where(actor_movies.c.actor_id == src.id).values(actor_id=keep.id))
        moved_movies += res.rowcount or 0

        # 2) 新作发现迁移（FK 级联会在删档案时清掉，必须先迁；video_code 重复的丢弃）
        keep_codes = select(NewRelease.video_code).where(
            NewRelease.actor_id == keep.id, NewRelease.video_code.isnot(None))
        db.execute(NewRelease.__table__.delete().where(
            NewRelease.actor_id == src.id, NewRelease.video_code.in_(keep_codes)))
        db.execute(NewRelease.__table__.update().where(NewRelease.actor_id == src.id).values(actor_id=keep.id))

        # 3) 订阅归并（受唯一约束，不能盲改 actor_id）
        src_sub = db.execute(select(Subscription).where(
            Subscription.sub_type == "actor", Subscription.actor_id == src.id)).scalars().first()
        keep_sub = db.execute(select(Subscription).where(
            Subscription.sub_type == "actor", Subscription.actor_id == keep.id)).scalars().first()
        if src_sub:
            if keep_sub is None:
                src_sub.actor_id = keep.id
                src_sub.name = keep.name
                moved_subs += 1
            else:
                keep_sub.auto_add = keep_sub.auto_add or src_sub.auto_add
                db.delete(src_sub)

        # 4) 字段归并：主档案为空才填（避免污染主档案已有资料）
        for f in ("name_en", "gender", "birth_date", "height", "cup", "measurements", "debut_date",
                  "bio", "timeline", "agency", "hobbies", "debut_work", "twitter", "website", "tags", "intro"):
            if not getattr(keep, f, None) and getattr(src, f, None):
                setattr(keep, f, getattr(src, f))
        if not keep.note and src.note:
            keep.note = src.note
        keep.movie_count = max(keep.movie_count or 0, src.movie_count or 0)
        keep.is_blacklisted = keep.is_blacklisted or src.is_blacklisted
        keep.works_fetched = keep.works_fetched or src.works_fetched
        keep.profile_fetched = keep.profile_fetched or src.profile_fetched

        # 5) 旧名留痕 → alias（'/' 分隔、去重、截 200）
        parts = [p.strip() for p in (keep.alias or "").split("/") if p.strip()]
        for cand in (src.name, src.name_en):
            if cand and cand != keep.name and cand not in parts:
                parts.append(cand)
                aliases_added.append(cand)
        keep.alias = ("/".join(parts)[:200] or None)

        # 6) 头像：主档案无本地文件时继承被合并档案的本地头像（commit 前落盘，URL 提交即可回源）
        src_local = _local_avatar(src.id)
        if keep_local is None and src_local is not None:
            dest = avatar_dir / f"actor-{keep.id}{src_local.suffix.lower()}"
            dest.write_bytes(src_local.read_bytes())
            keep.avatar_url = f"/api/images/avatars/actor-{keep.id}{dest.suffix}?v={int(datetime.utcnow().timestamp())}"
            keep_local = dest
            avatar = "copied"
        elif not keep.avatar_url and src.avatar_url:
            keep.avatar_url = src.avatar_url
            avatar = "inherited"

        # 7) 删除被合并档案（残余关联行走 DB 级联清理）
        db.delete(src)

    db.commit()  # 单事务一次提交；文件清理放提交后

    # 7) 清理被合并档案的头像文件（内容已按需转移）
    for sid in payload.source_ids:
        for p in avatar_dir.glob(f"actor-{sid}.*"):
            try:
                p.unlink()
            except Exception:
                pass

    return {"ok": True, "moved_movies": moved_movies, "moved_subs": moved_subs,
            "aliases_added": aliases_added, "avatar": avatar}


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
    # 男演员不抓取资料（三源均为女优库）
    if (actor.gender or "").lower() == "male":
        return {"ok": False, "source": None, "message": "男演员不抓取资料"}
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
                # 头像只填空缺：已有头像（含手动更换的）不覆盖
                if k == "avatar_url" and actor.avatar_url:
                    continue
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


@router.post("/extract-profiles")
def start_extract_profiles(_user: CurrentUser):
    """一键提取全部待抓演员信息（后台线程批量执行，切走页面不中断）。"""
    from services import actor_profile_batch
    ok, msg = actor_profile_batch.start()
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    return {"ok": True, "message": msg}


@router.get("/extract-profiles/status")
def extract_profiles_status(_user: CurrentUser):
    """一键提取任务进度（前端轮询）。"""
    from services import actor_profile_batch
    return actor_profile_batch.status()


@router.get("/{actor_id}/profile-insights")
def actor_insights(actor_id: int, db: DbSession, _user: CurrentUser):
    """演员画像（F11）：作品年份分布 + 共演演员 Top10。"""
    from collections import Counter
    from models import ActorMovie

    rows = db.execute(
        select(Task.release_date, Task.actors).join(ActorMovie, ActorMovie.task_id == Task.id)
        .where(ActorMovie.actor_id == actor_id, Task.release_date.isnot(None))
    ).all()
    years: dict[str, int] = {}
    co: Counter = Counter()
    for rd, actors in rows:
        y = (rd or "")[:4]
        if y:
            years[y] = years.get(y, 0) + 1
        for a in (actors or "").split(","):
            a = a.strip()
            if a:
                co[a] += 1
    from models import Actor
    actor = db.get(Actor, actor_id)
    if actor and actor.name in co:
        del co[actor.name]
    return {
        "years": [{"year": k, "count": v} for k, v in sorted(years.items())],
        "co_stars": [{"name": k, "count": v} for k, v in co.most_common(10)],
    }


@router.get("/{actor_id}/profile-insights")
def actor_insights(actor_id: int, db: DbSession, _user: CurrentUser):
    """演员画像（F11）：作品年份分布 + 共演演员 Top10。"""
    from collections import Counter
    from models import ActorMovie

    rows = db.execute(
        select(Task.release_date, Task.actors).join(ActorMovie, ActorMovie.task_id == Task.id)
        .where(ActorMovie.actor_id == actor_id, Task.release_date.isnot(None))
    ).all()
    years: dict[str, int] = {}
    co: Counter = Counter()
    for rd, actors in rows:
        y = (rd or "")[:4]
        if y:
            years[y] = years.get(y, 0) + 1
        for a in (actors or "").split(","):
            a = a.strip()
            if a:
                co[a] += 1
    from models import Actor
    actor = db.get(Actor, actor_id)
    if actor and actor.name in co:
        del co[actor.name]
    return {
        "years": [{"year": k, "count": v} for k, v in sorted(years.items())],
        "co_stars": [{"name": k, "count": v} for k, v in co.most_common(10)],
    }


@router.get("/status-summary")
def actor_status_summary(db: DbSession, _user: CurrentUser, ids: str = Query("", description="逗号分隔的 actor_id 列表")):
    """N9: 批量返回演员状态（最后作品日期/距今天数），用于订阅页休止徽标。"""
    from datetime import datetime
    from models import ActorMovie

    parsed = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    if not parsed:
        return {"ok": True, "items": {}}
    rows = db.execute(
        select(ActorMovie.actor_id, Task.release_date)
        .join(Task, Task.id == ActorMovie.task_id)
        .where(ActorMovie.actor_id.in_(parsed), Task.release_date.isnot(None))
        .order_by(Task.release_date.desc())
    ).all()
    items: dict[int, dict] = {}
    now = datetime.utcnow()
    for aid, rd in rows:
        if aid in items:
            continue
        days = None
        try:
            days = (now - datetime.strptime(str(rd)[:10], "%Y-%m-%d")).days
        except Exception:
            pass
        items[aid] = {"last_release": str(rd)[:10], "days_since": days}
    return {"ok": True, "items": items}
