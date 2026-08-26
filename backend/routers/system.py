"""系统路由 —— 磁盘信息等。兼容 AVDB 前端 GET /api/system/disk。"""

from __future__ import annotations

import json
import shutil
from fastapi import APIRouter, Query
from config import get_settings
from deps import CurrentUser, CurrentAdmin, DbSession

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/login-wall")
def login_wall(limit: int = Query(10, ge=1, le=30)):
    """登录页海报墙：订阅演员作品的远程海报 URL（随机抽样）。

    免鉴权 —— 登录页没有 token。只返回远程 URL（c0.jdbstatic），
    不暴露本地文件路径/番号等元数据。
    """
    from sqlalchemy import func, select
    from database import SessionLocal
    from models import Subscription, Task, actor_movies

    db = SessionLocal()
    try:
        rows = db.execute(
            select(Task.thumbnail_urls, Task.poster_url)
            .join(actor_movies, actor_movies.c.task_id == Task.id)
            .join(Subscription, Subscription.actor_id == actor_movies.c.actor_id)
            .where(
                Subscription.sub_type == "actor",
                Subscription.enabled == True,  # noqa: E712
                Task.status == "visited",
                Task.thumbnail_urls.isnot(None),
            )
            .order_by(func.random())
            .limit(limit)
        ).all()
        urls: list[str] = []
        for thumbs, poster in rows:
            u = None
            try:
                arr = json.loads(thumbs or "[]")
                if isinstance(arr, list) and arr:
                    u = arr[0]
            except Exception:
                pass
            u = u or poster
            if u and u not in urls:
                urls.append(u)
        return {"wall": urls}
    except Exception:
        return {"wall": []}
    finally:
        db.close()


@router.get("/disk")
def disk_info(_user: CurrentUser):
    """磁盘使用情况。返回前端 DiskInfo 期望的嵌套结构。"""
    data_dir = get_settings().DATA_DIR
    try:
        usage = shutil.disk_usage(data_dir)
        total_gb = round(usage.total / 1024**3, 1)
        used_gb = round(usage.used / 1024**3, 1)
        free_gb = round(usage.free / 1024**3, 1)
        free_percent = round((1 - usage.used / usage.total) * 100, 1)
    except Exception:
        total_gb = used_gb = free_gb = free_percent = 0

    import os
    images_dir = os.path.join(data_dir, "images")
    images_size = 0
    images_count = 0
    if os.path.isdir(images_dir):
        for root, _, files in os.walk(images_dir):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    images_size += os.path.getsize(fp)
                    images_count += 1
                except Exception:
                    pass

    db_path = os.path.join(data_dir, "javdb.db")
    db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0

    return {
        "data": {
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "free_percent": free_percent,
        },
        "images_size_mb": round(images_size / 1024 / 1024, 1),
        "images_count": images_count,
        "db_size_mb": round(db_size / 1024 / 1024, 1),
    }


@router.get("/logs")
def app_logs(_user: CurrentAdmin, limit: int = 100, filter: str = "", file: str = "app"):
    """读取日志文件最后 N 行。

    file=app: data/app.log（应用日志）
    file=scraper: data/scraper_stderr.log（scraper 子进程 stdout/stderr）
    file=downloaders: data/downloaders.log（下载器日志）
    file=actor_profile: data/actor_profile.log（演员资料聚合日志）
    """
    from pathlib import Path
    filenames = {"app": "app.log", "scraper": "scraper_stderr.log", "downloaders": "downloaders.log",
                 "actor_profile": "actor_profile.log"}
    filename = filenames.get(file, "app.log")
    log_path = Path(get_settings().DATA_DIR) / filename
    if not log_path.exists():
        return {"lines": [], "total": 0, "file": filename}
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if filter:
            lines = [l for l in lines if filter.lower() in l.lower()]
        return {"lines": lines[-limit:], "total": len(lines), "file": filename}
    except Exception as e:
        return {"lines": [], "total": 0, "error": str(e), "file": filename}


@router.get("/status")
def system_status(db: DbSession, _user: CurrentUser):
    """N14: 系统状态全景——调度任务/队列/活跃下载/最近错误/备份。"""
    from datetime import datetime, timedelta
    from sqlalchemy import select
    from models import CrawlLog, Download, NotifyLog, Task

    jobs = []
    try:
        from services.scheduler import list_jobs
        jobs = list_jobs()
    except Exception:
        pass

    now = datetime.utcnow()
    day_ago = now - timedelta(hours=24)
    pending = db.execute(select(Task.id).where(Task.status == "pending")).all()
    failed = db.execute(select(Task.id).where(Task.status == "failed")).all()
    active_dl = db.execute(
        select(Download.id).where(Download.status.in_(["pushed", "downloading"]))
    ).all()
    crawl_err = db.execute(
        select(CrawlLog.id).where(CrawlLog.created_at >= day_ago, CrawlLog.level.in_(["error", "warn"]))
    ).all()
    notify_fail = db.execute(
        select(NotifyLog.id).where(NotifyLog.created_at >= day_ago, NotifyLog.ok == False)  # noqa: E712
    ).all()

    backups = []
    try:
        from config import get_settings
        import os
        bdir = os.path.join(get_settings().DATA_DIR, "backups")
        if os.path.isdir(bdir):
            files = sorted(os.listdir(bdir), reverse=True)[:5]
            backups = [{"name": f, "size_mb": round(os.path.getsize(os.path.join(bdir, f)) / 1048576, 1)} for f in files if os.path.isfile(os.path.join(bdir, f))]
    except Exception:
        pass

    return {
        "ok": True,
        "jobs": jobs,
        "queue": {"pending": len(pending), "failed": len(failed)},
        "active_downloads": len(active_dl),
        "errors_24h": {"crawl": len(crawl_err), "notify": len(notify_fail)},
        "backups": backups,
        "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.get("/s3-status")
def s3_status(db: DbSession, _user: CurrentAdmin):
    """N20: 对象存储备份状态——配置是否完整 + 远端备份列表。"""
    from services.s3_backup import _s3_client, list_remote

    client, _bucket = _s3_client() if _s3_client() else (None, None)
    remote = list_remote() if client else {"ok": False, "message": "S3 未配置"}
    return {"ok": True, "configured": client is not None, "remote": remote}


@router.post("/s3-upload")
async def s3_upload(_user: CurrentAdmin):
    """N20: 手动上传最新一份本地备份到对象存储。"""
    import glob
    from config import get_settings
    from services.s3_backup import upload_backup

    bdir = os.path.join(get_settings().DATA_DIR, "backups")
    files = sorted(glob.glob(os.path.join(bdir, "javdb-*.db")) + glob.glob(os.path.join(bdir, "javdb-*.db.enc")), reverse=True)
    if not files:
        return {"ok": False, "message": "本地无备份文件"}
    return await upload_backup(files[0])


@router.get("/download-strategy")
def get_download_strategy(db: DbSession, _user: CurrentAdmin):
    """N23: 读取下载策略（JSON）。"""
    from services.download_strategy import get_strategy
    return {"ok": True, "strategy": get_strategy(db)}


@router.put("/download-strategy")
def set_download_strategy(payload: dict, db: DbSession, _user: CurrentAdmin):
    """N23: 保存下载策略（{actors: {name: downloader}, makers: {...}, default: ...}）。"""
    import json
    from models import Setting
    row = db.get(Setting, "download_strategy")
    value = json.dumps(payload.get("strategy") or {}, ensure_ascii=False)
    if row:
        row.value = value
    else:
        db.add(Setting(key="download_strategy", value=value))
    db.commit()
    return {"ok": True}
