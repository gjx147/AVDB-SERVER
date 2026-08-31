"""任务管理路由 —— 列表/详情/统计/删除（含级联）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import update, func, or_, select
from sqlalchemy.orm import Session

from deps import CurrentUser, DbSession, Pagination, CurrentAdmin
from models import Task, Actor
from schemas import TaskListResponse, TaskOut

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


# ── Phase 2 锁体系：单任务提取的进程回收助手 ──

def _kill_process_tree(proc: subprocess.Popen) -> None:
    """杀整个进程树（包括 Playwright Chromium 子进程）。对齐 auto_crawl._kill_process_tree。"""
    from services import scraper_lock
    if not scraper_lock.is_proc_alive(proc):
        return  # 已退出
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10,
            )
        else:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _reap_and_clear(proc: subprocess.Popen, timeout: int = 1800) -> None:
    """后台线程：等待 extract-single 子进程；超时整树杀；按身份释放全局锁。

    timeout 默认 1800s（30 分钟），对齐 crawl.py _DEFAULT_TIMEOUT。
    """
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
    except Exception:
        pass
    finally:
        # Phase 2 P1-3：按身份释放（get_proc() is proc 才 clear，防 ABA）
        from services import scraper_lock
        if scraper_lock.get_proc() is proc:
            scraper_lock.clear()


# ── 静态路由（必须在 /{task_id} 之前！）──

@router.get("", response_model=TaskListResponse)
def list_tasks(
    db: DbSession, _user: CurrentUser, pagination: Pagination,
    list_source_id: int | None = Query(None), status: str | None = Query(None),
    view_status: str | None = Query(None), is_favorite: bool | None = Query(None),
):
    stmt = select(Task); count_stmt = select(func.count(Task.id))
    if list_source_id is not None: stmt = stmt.where(Task.list_source_id == list_source_id); count_stmt = count_stmt.where(Task.list_source_id == list_source_id)
    if status: stmt = stmt.where(Task.status == status); count_stmt = count_stmt.where(Task.status == status)
    if view_status: stmt = stmt.where(Task.view_status == view_status); count_stmt = count_stmt.where(Task.view_status == view_status)
    if is_favorite is not None: stmt = stmt.where(Task.is_favorite == is_favorite); count_stmt = count_stmt.where(Task.is_favorite == is_favorite)
    offset, limit = pagination
    total = db.execute(count_stmt).scalar_one()
    items = db.execute(stmt.order_by(Task.id.desc()).offset(offset).limit(limit)).scalars().all()
    return TaskListResponse(total=total, page=offset//limit+1, page_size=limit, items=items)


@router.get("/stats")
def task_stats(db: DbSession, _user: CurrentUser):
    rows = db.execute(select(Task.status, func.count(Task.id)).group_by(Task.status)).all()
    by_status = {r[0]: r[1] for r in rows}
    total = sum(by_status.values())
    viewed = db.execute(select(func.count(Task.id)).where(Task.view_status == "viewed")).scalar_one()
    favorite = db.execute(select(func.count(Task.id)).where(Task.is_favorite == True)).scalar_one()  # noqa: E712
    return {"total": total, "by_status": by_status, "viewed": viewed, "favorite": favorite}


@router.post("/batch-delete")
@router.post("/batch/delete")  # 兼容前端旧路径
def batch_delete(task_ids: list[int], db: DbSession, _user: CurrentUser):
    if not task_ids: return {"ok": True, "deleted": 0}
    deleted = db.execute(Task.__table__.delete().where(Task.id.in_(task_ids))).rowcount
    db.commit()
    return {"ok": True, "deleted": deleted}


@router.post("/batch/retry")
def batch_retry(task_ids: list[int], db: DbSession, _user: CurrentUser):
    """批量重置失败任务为 pending。"""
    updated = db.execute(
        Task.__table__.update().where(
            Task.id.in_(task_ids), Task.status == "failed"
        ).values(status="pending")  # T11: 不重置 retry_count，避免与 auto_retry 构成无限重试
    ).rowcount
    db.commit()
    return {"ok": True, "updated": updated}


@router.post("/batch/retry-now")
def batch_retry_now(db: DbSession, _user: CurrentUser):
    """一键重试全部失败+待处理任务：failed→pending 并【立即】触发对应源的提取子进程。

    与 /batch/retry（只翻状态等周期）的区别：此处点击后马上起 scraper，
    TOP250 源走 top250 通道、其余走 main 通道；通道忙碌或 JavDB 登录中
    时跳过立即启动——由 extract 周期（10 分钟内）兜底接管。
    """
    from routers.crawl import _start_scraper_guarded
    from services.scraper_lock import is_running, CHANNEL_TOP250
    from models import ListSource
    # 失败 + 待处理 一并纳入（pending 无需翻状态，直接进入提取范围）
    rows = db.execute(
        select(Task.id, Task.list_source_id, Task.status).where(
            Task.status.in_(["failed", "pending"]))
    ).all()
    if not rows:
        return {"ok": True, "updated": 0, "total": 0, "started": [], "busy": []}
    failed_ids = [r[0] for r in rows if r[2] == "failed"]
    updated = 0
    if failed_ids:
        updated = db.execute(
            Task.__table__.update().where(Task.id.in_(failed_ids), Task.status == "failed")
            .values(status="pending")  # 同 /batch/retry：不重置 retry_count（防死循环）
        ).rowcount
    db.commit()
    started: list[str] = []
    busy: list[str] = []
    for sid in sorted({r[1] for r in rows if r[1] is not None}):
        src = db.get(ListSource, sid)
        name = src.list_code if src else f"#{sid}"
        ch = CHANNEL_TOP250 if (src and src.list_code == "TOP250") else "main"
        if is_running(ch):
            busy.append(name)
            continue
        try:
            _start_scraper_guarded(
                ["extract", "--list-source-id", str(sid)],
                {"mode": "extract", "list_source_id": sid, "auto": "retry-now",
                 "started_at": datetime.utcnow().isoformat()},
                channel=ch,
            )
            started.append(name)
        except HTTPException:
            busy.append(name)  # 409：通道被占/登录中
        except Exception:
            busy.append(name)
    return {"ok": True, "updated": updated, "total": len(rows),
            "started": started, "busy": busy}


@router.post("/batch/force-visit")
def batch_force_visit(task_ids: list[int], db: DbSession, _user: CurrentUser):
    """把失败任务强制标记为已入库（手动确认，不爬取元数据/磁力）。

    适用：确认无法再提取的任务（源站失效/永不重试），从失败队列清出。
    仅作用于 status='failed' 的任务；retry_count 不动（历史留痕）。
    """
    updated = db.execute(
        Task.__table__.update().where(
            Task.id.in_(task_ids), Task.status == "failed"
        ).values(status="visited")
    ).rowcount
    db.commit()
    return {"ok": True, "updated": updated}


@router.post("/batch/favorite")
def batch_favorite(task_ids: list[int], db: DbSession, _user: CurrentUser):
    """批量设为收藏。"""
    from datetime import datetime
    updated = db.execute(
        Task.__table__.update().where(Task.id.in_(task_ids))
        .values(is_favorite=True, favorite_at=datetime.utcnow())
    ).rowcount
    db.commit()
    return {"ok": True, "updated": updated}


@router.get("/search")
def search_tasks(db: DbSession, _user: CurrentUser, q: str = Query(..., min_length=1),
                 status: str | None = Query(None), skip: int = Query(0, ge=0), limit: int = Query(48, ge=1, le=200)):
    stmt = select(Task).where(or_(Task.title.like(f"%{q}%"), Task.video_code.like(f"%{q}%")))
    if status: stmt = stmt.where(Task.status == status)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = db.execute(stmt.order_by(Task.id.desc()).offset(skip).limit(limit)).scalars().all()
    return {"total": total, "items": items, "q": q}


@router.get("/search/count")
def search_count(db: DbSession, _user: CurrentUser, q: str = Query(..., min_length=1), status: str | None = Query(None)):
    stmt = select(func.count(Task.id)).where(or_(Task.title.like(f"%{q}%"), Task.video_code.like(f"%{q}%")))
    if status: stmt = stmt.where(Task.status == status)
    return {"count": db.execute(stmt).scalar_one()}


@router.get("/favorites/list")
def list_favorites_tasks(db: DbSession, _user: CurrentUser, skip: int = Query(0, ge=0), limit: int = Query(48, ge=1, le=200),
                         in_library: bool | None = Query(None, description="按 Emby 在库状态筛选")):
    stmt = select(Task).where(Task.is_favorite == True)  # noqa: E712
    if in_library is not None:
        stmt = stmt.where(Task.media_in_library == in_library)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = db.execute(stmt.order_by(Task.favorite_at.desc().nullslast(), Task.id.desc()).offset(skip).limit(limit)).scalars().all()
    return {"total": total, "items": items}


# ── 动态路由 /{task_id} 及其子路由 ──

@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: DbSession, _user: CurrentUser):
    task = db.get(Task, task_id)
    if not task: raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/{task_id}/cast")
def task_cast(task_id: int, db: DbSession, _user: CurrentUser):
    """返回 task 的关联演员 [{id, name, avatar_url}]，按名字查 actors 表。

    task.actors 是逗号分隔的名字字符串，这里批量匹配 actors 表拿头像。
    匹配策略：精确 name 批量查 → 未命中的 LIKE 兜底。避免 N+1 查询。
    """
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not task.actors:
        return []
    names = [n.strip() for n in task.actors.split(",") if n.strip()][:15]
    if not names:
        return []
    # 批量精确查询（1 次 SQL 替代最多 15 次）
    exact_rows = db.execute(select(Actor).where(Actor.name.in_(names))).scalars().all()
    exact_map = {a.name: a for a in exact_rows}
    # 对未精确命中的名字做 LIKE 兜底（每个一次，但通常很少）
    missing = [n for n in names if n not in exact_map]
    for name in missing:
        actor = db.execute(
            select(Actor).where(Actor.name.like(f"%{name}%")).limit(1)
        ).scalar_one_or_none()
        if actor:
            exact_map[name] = actor
    # 按原始顺序返回
    result = []
    for name in names:
        actor = exact_map.get(name)
        if actor:
            result.append({"id": actor.id, "name": actor.name, "avatar_url": actor.avatar_url})
        else:
            result.append({"id": None, "name": name, "avatar_url": None})
    return result


@router.post("/{task_id}/extract")
def extract_single(task_id: int, db: DbSession, _user: CurrentUser):
    """触发单任务提取（fire-and-forget subprocess）。

    Phase 2 锁体系修复：
    - 走 scraper_lock 原子获取+注册（不再绕过全局锁）
    - 进程组隔离（Windows CREATE_NEW_PROCESS_GROUP / Unix start_new_session），支持整树杀
    - 后台线程超时回收（默认 30 分钟，对齐 auto_crawl._kill_process_tree 思路）
    - 退出/超时后按身份 clear（防 ABA）
    """
    import threading
    task = db.get(Task, task_id)
    if not task: raise HTTPException(status_code=404, detail="任务不存在")
    # 异步触发 scraper extract-single
    from config import get_settings
    settings = get_settings()
    scraper = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "magnet_scraper", "scraper.py")
    python = settings.SCRAPER_PYTHON or sys.executable
    try:
        # 关键修复：从 DB 注入 http_proxy + javdb_url 到子进程 env
        # （否则 scraper 的 os.environ.get("HTTP_PROXY") 为空，Chromium 无代理被 Cloudflare 拦截）
        _env = dict(os.environ)
        # Phase 2 F07：注入回调共享密钥（register/unregister 回调鉴权）
        from services import scraper_lock
        _env["SCRAPER_CALLBACK_TOKEN"] = scraper_lock.get_callback_token()
        from models import Setting
        for _key in ("http_proxy",):
            _row = db.get(Setting, _key)
            if _row and _row.value:
                _val = _row.value.strip()
                _env["HTTP_PROXY"] = _val
                _env["HTTPS_PROXY"] = _val
                _env["http_proxy"] = _val
                _env["https_proxy"] = _val
        _row = db.get(Setting, "javdb_url")
        if _row and _row.value:
            _env["JAVDB_URL"] = _row.value.strip()

        # 进程组隔离：支持整树 kill（对齐 crawl.py _start_scraper / auto_crawl）
        popen_kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "env": _env,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(
            [python, scraper, "extract-single", "--url", task.url],
            **popen_kwargs,
        )

        # 原子获取+注册全局锁；被占用则回收刚启动的进程（Phase 2 P1-4）
        from services import scraper_lock
        if not scraper_lock.try_acquire_and_set(proc, {
            "mode": "extract-single", "pid": proc.pid, "task_id": task_id,
            "started_at": datetime.utcnow().isoformat(),
        }):
            _kill_process_tree(proc)
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            return {"ok": False, "message": "已有爬取任务在运行"}

        # 后台线程等待 + 超时整树杀 + 按身份释放锁
        threading.Thread(target=_reap_and_clear, args=(proc,), daemon=True).start()
    except Exception as e:
        return {"ok": False, "message": str(e)}
    return {"ok": True, "message": "已触发提取"}


@router.delete("/{task_id}")
def delete_task(task_id: int, db: DbSession, _user: CurrentUser):
    task = db.get(Task, task_id)
    if not task: raise HTTPException(status_code=404, detail="任务不存在")
    db.delete(task); db.commit()
    return {"ok": True, "message": "已删除"}


@router.post("/{task_id}/favorite")
def toggle_favorite(task_id: int, db: DbSession, _user: CurrentUser):
    task = db.get(Task, task_id)
    if not task: raise HTTPException(status_code=404, detail="任务不存在")
    task.is_favorite = not task.is_favorite
    task.favorite_at = datetime.utcnow() if task.is_favorite else None
    db.commit()
    return {"ok": True, "is_favorite": task.is_favorite}


@router.delete("/{task_id}/favorite")
def unfavorite(task_id: int, db: DbSession, _user: CurrentUser):
    """取消收藏（幂等）。前端取消收藏走 DELETE；POST 保留 toggle 兼容旧调用。"""
    task = db.get(Task, task_id)
    if not task: raise HTTPException(status_code=404, detail="任务不存在")
    task.is_favorite = False
    task.favorite_at = None
    db.commit()
    return {"ok": True, "is_favorite": False}


@router.patch("/{task_id}/view-status")
def set_view_status(task_id: int, status: str, db: DbSession, _user: CurrentUser):
    valid = {"viewed", "browsed", "want", ""}
    if status not in valid: raise HTTPException(status_code=400, detail=f"无效状态，可选: {valid - {''}}")
    task = db.get(Task, task_id)
    if not task: raise HTTPException(status_code=404, detail="任务不存在")
    task.view_status = status or None
    task.viewed_at = datetime.utcnow() if status == "viewed" else task.viewed_at
    db.commit()
    return {"ok": True, "view_status": task.view_status}


@router.get("/{task_id}/magnets")
def get_magnets(task_id: int, db: DbSession, _user: CurrentUser):
    task = db.get(Task, task_id)
    if not task: raise HTTPException(status_code=404, detail="任务不存在")
    magnets = []
    if task.magnets_json:
        try:
            raw = json.loads(task.magnets_json)
            if isinstance(raw, list): magnets = raw
        except json.JSONDecodeError: pass
    if not magnets and task.best_magnet: magnets = [task.best_magnet]
    return {"magnets": magnets, "video_code": task.video_code}


@router.patch("/{task_id}/note")
def update_note(task_id: int, db: DbSession, _user: CurrentUser, note: str = Query("")):
    task = db.get(Task, task_id)
    if not task: raise HTTPException(status_code=404, detail="任务不存在")
    task.note = note or None
    db.commit()
    return {"ok": True}



class BatchViewRequest(BaseModel):
    task_ids: list[int]
    status: str = "viewed"
    downloader: str | None = None  # 强制指定下载器（clouddrive/qbittorrent）；None=智能策略路由


@router.post("/batch-view")
def batch_set_view(payload: BatchViewRequest, db: DbSession, _user: CurrentUser):
    """批量标记已看/想看（F6）。"""
    if payload.status not in ("viewed", "want"):
        raise HTTPException(status_code=400, detail="status 仅支持 viewed / want")
    if not payload.task_ids:
        return {"ok": True, "updated": 0}
    n = db.execute(
        update(Task).where(Task.id.in_(payload.task_ids)).values(view_status=payload.status)
    ).rowcount
    db.commit()
    return {"ok": True, "updated": n}


@router.post("/batch-push")
async def batch_push(payload: BatchViewRequest, db: DbSession, _user: CurrentUser):
    """批量推送下载（F6）：把选中任务（需已有磁力）推送到下载器。
    payload.downloader 强制指定（clouddrive/qbittorrent，如演员页批量推送 CD2）；
    不传则按智能策略路由（演员/厂牌优先，否则默认下载器）。"""
    force_dl = (payload.downloader or "").strip().lower() or None
    if force_dl and force_dl not in ("clouddrive", "qbittorrent"):
        raise HTTPException(status_code=400, detail="downloader 仅支持 clouddrive / qbittorrent")
    from models import Download
    from routers.downloaders import _extract_hash, _get_setting, _push_clouddrive, _push_qbittorrent

    tasks = db.execute(
        select(Task).where(Task.id.in_(payload.task_ids))
    ).scalars().all()
    config_keys = (
        "qb_url", "qb_username", "qb_password", "qbittorrent_save_path",
        "clouddrive_url", "clouddrive_token", "clouddrive_username",
        "clouddrive_password", "clouddrive_save_path",
    )
    config = {k: _get_setting(db, k) for k in config_keys}
    # N23: 按智能策略路由下载器（演员/厂牌优先，否则默认）
    # 打磨：策略与默认下载器循环外读取一次，避免每任务重复查询
    from services.download_strategy import get_strategy, pick_downloader
    strategy = get_strategy(db)
    default_dl = _get_setting(db, "default_downloader") or "qbittorrent"
    pushed = 0
    skipped = 0
    for t in tasks:
        if not t.best_magnet:
            skipped += 1
            continue
        dl = force_dl or pick_downloader(db, t, strategy, default_dl)
        try:
            if dl == "clouddrive":
                result = await _push_clouddrive(t.best_magnet, config)
            else:
                result = await _push_qbittorrent(t.best_magnet, config)
            if result.get("ok"):
                db.add(Download(
                    task_id=t.id, video_code=t.video_code, magnet=t.best_magnet,
                    info_hash=_extract_hash(t.best_magnet), downloader=dl,
                    status="pushed",
                ))
                pushed += 1
            else:
                skipped += 1
        except Exception:
            skipped += 1
    db.commit()
    return {"ok": True, "pushed": pushed, "skipped": skipped}


@router.post("/dedupe")
def dedupe_tasks(db: DbSession, _user: CurrentAdmin, dry_run: bool = Query(True)):
    """F13: 番号归一化去重——按 normalize_code 分组，保留磁力/评分最全者，
    迁移收藏/共演/下载/上新引用后删除重复任务。dry_run=true 只预览计划。"""
    from sqlalchemy import update as sa_update
    from models import Download, NewRelease, actor_movies, task_collections
    from services.media_server import normalize_code

    tasks = db.execute(select(Task).where(Task.video_code.isnot(None))).scalars().all()
    groups: dict[str, list[Task]] = {}
    for t in tasks:
        key = normalize_code(t.video_code or "")
        if key:
            groups.setdefault(key, []).append(t)
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}

    def _pick(group: list[Task]):
        ordered = sorted(group, key=lambda t: (1 if t.best_magnet else 0, t.rating or 0, -t.id), reverse=True)
        return ordered[0], ordered[1:]

    plan = []
    for code, group in dup_groups.items():
        keep, dups = _pick(group)
        plan.append({"code": code, "keep_id": keep.id, "dup_ids": [d.id for d in dups], "dup_count": len(dups)})
    if dry_run:
        return {"ok": True, "dry_run": True, "groups": len(plan),
                "to_delete": sum(len(p["dup_ids"]) for p in plan), "plan": plan}

    deleted = 0
    for p in plan:
        keep_id = p["keep_id"]
        dup_ids = p["dup_ids"]
        for dup_id in dup_ids:
            for table in (actor_movies, task_collections):
                db.execute(table.update().where(table.c.task_id == dup_id).values(task_id=keep_id))
            db.execute(sa_update(Download).where(Download.task_id == dup_id).values(task_id=keep_id))
            db.execute(sa_update(NewRelease).where(NewRelease.task_id == dup_id).values(task_id=keep_id))
        db.execute(Task.__table__.delete().where(Task.id.in_(dup_ids)))
        deleted += len(dup_ids)
    db.commit()
    return {"ok": True, "dry_run": False, "groups": len(plan), "deleted": deleted}


@router.get("/wishlist-gaps")
def wishlist_gaps(db: DbSession, _user: CurrentUser, limit: int = Query(50, le=100)):
    """N13: 观看缺口清单——想看（want）但无磁力或不在库的作品，可批量推送。"""
    rows = db.execute(
        select(Task).where(Task.view_status == "want")
        .order_by(Task.rating.desc().nullslast(), Task.release_date.desc().nullslast())
        .limit(limit)
    ).scalars().all()
    items = [
        {
            "task_id": t.id, "video_code": t.video_code, "title": t.title, "rating": t.rating,
            "has_magnet": bool(t.best_magnet), "in_library": bool(t.media_in_library),
        }
        for t in rows
    ]
    return {"ok": True, "total": len(items), "items": items}
