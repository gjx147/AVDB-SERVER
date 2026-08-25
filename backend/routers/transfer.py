"""数据导入导出（F1）：任务 CSV 导出 / 订阅清单导出 / 番号批量导入。"""
from __future__ import annotations

import csv
import io as _io
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from deps import CurrentUser, DbSession
from models import ListSource, Subscription, Task

export_router = APIRouter(prefix="/api/export", tags=["export"])
import_router = APIRouter(prefix="/api/import", tags=["import"])


@export_router.get("/tasks.csv")
def export_tasks_csv(db: DbSession, _user: CurrentUser):
    """导出全库任务为 CSV（utf-8-sig BOM，Excel 直接打开中文不乱码）。"""
    tasks = db.execute(select(Task).order_by(Task.id)).scalars().all()
    buf = _io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "video_code", "title", "url", "status", "rating", "maker",
        "label", "series", "actors", "release_date", "created_at",
    ])
    for t in tasks:
        writer.writerow([
            t.id, t.video_code, t.title, t.url, t.status, t.rating, t.maker,
            t.label, t.series, t.actors, t.release_date, t.created_at,
        ])
    data = buf.getvalue()
    fname = f"avdb_tasks_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        content=data.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@export_router.get("/subscriptions.json")
def export_subscriptions_json(db: DbSession, _user: CurrentUser):
    """导出订阅清单（含演员名字），用于备份/迁移。"""
    from models import Actor
    subs = db.execute(select(Subscription)).scalars().all()
    actor_ids = {s.actor_id for s in subs if s.actor_id}
    actors: dict[int, str] = {}
    if actor_ids:
        actors = {
            a.id: a.name
            for a in db.execute(select(Actor).where(Actor.id.in_(actor_ids))).scalars()
        }
    items = []
    for s in subs:
        items.append({
            "name": s.name,
            "sub_type": s.sub_type,
            "actor_id": s.actor_id,
            "actor_name": actors.get(s.actor_id) if s.actor_id else None,
            "rank_type": s.rank_type,
            "auto_add": s.auto_add,
            "enabled": s.enabled,
            "check_interval_hours": s.check_interval_hours,
        })
    return {"subscriptions": items, "exported_at": str(datetime.utcnow())}


class ImportCodesRequest(BaseModel):
    codes: list[str]


@import_router.post("/codes")
def import_codes(payload: ImportCodesRequest, db: DbSession, _user: CurrentUser):
    """批量导入番号建 pending 任务（去重，RANKING 源）。"""
    codes = [c.strip().upper() for c in payload.codes if c and c.strip()]
    if not codes:
        raise HTTPException(status_code=400, detail="没有有效的番号")
    if len(codes) > 2000:
        raise HTTPException(status_code=400, detail="单次最多导入 2000 个番号")
    src = db.execute(
        select(ListSource).where(ListSource.list_code == "RANKING")
    ).scalar_one_or_none()
    if not src:
        src = ListSource(list_code="RANKING", list_path="/rankings")
        db.add(src)
        db.flush()
    existing = {
        r[0] for r in db.execute(
            select(Task.video_code).where(Task.video_code.in_(codes))
        ).all()
    }
    added = 0
    skipped = 0
    for code in codes:
        if code in existing:
            skipped += 1
            continue
        db.add(Task(list_source_id=src.id, url=f"/v/{code}", video_code=code, status="pending"))
        existing.add(code)
        added += 1
    db.commit()
    return {"ok": True, "added": added, "skipped": skipped}


class ImportMagnetsRequest(BaseModel):
    text: str = Field(max_length=20000, description="含磁力链接的文本（支持多行）")


@import_router.post("/magnets")
def import_magnets(payload: ImportMagnetsRequest, db: DbSession, _user: CurrentUser):
    """N11: 批量导入磁力链接——解析 btih、按 info_hash 去重、建 pending 任务。"""
    import re as _re
    from models import Download, ListSource, Task

    magnets = _re.findall(r"magnet:\?[^\s]+", payload.text)
    hashes = []
    for m in magnets:
        hm = _re.search(r"btih:([a-fA-F0-9]{40})", m)
        if hm:
            hashes.append((hm.group(1).lower(), m))
    if not hashes:
        return {"ok": False, "message": "未解析到有效磁力链接（需含 btih 哈希）", "added": 0, "skipped": 0}

    # 去重：downloads.info_hash + tasks.best_magnet
    known_hashes = {r[0].lower() for r in db.execute(select(Download.info_hash)).all() if r[0]}
    task_hashes = set()
    for (m,) in db.execute(select(Task.best_magnet).where(Task.best_magnet.isnot(None))).all():
        if m:
            hm = _re.search(r"btih:([a-fA-F0-9]{40})", m)
            if hm:
                task_hashes.add(hm.group(1).lower())

    src = db.execute(select(ListSource).where(ListSource.list_code == "RANKING")).scalar_one_or_none()
    if not src:
        src = ListSource(list_code="RANKING", list_path="/rankings")
        db.add(src)
        db.flush()

    added = 0
    skipped = 0
    seen: set[str] = set()
    for h, m in hashes:
        if h in known_hashes or h in task_hashes or h in seen:
            skipped += 1
            continue
        seen.add(h)
        code = None
        m2 = _re.search(r"dn=([^&]+)", m)
        if m2:
            from urllib.parse import unquote
            code = _re.search(r"[A-Z]{2,5}-\d{2,5}[A-Z0-9]?", unquote(m2.group(1)).upper())
            code = code.group(0) if code else None
        db.add(Task(
            list_source_id=src.id,
            url=f"/m/{h[:12]}",
            video_code=code or h[:12].upper(),
            best_magnet=m,
            magnets_json="[]",
            status="pending",
        ))
        added += 1
    db.commit()
    return {"ok": True, "added": added, "skipped": skipped, "total": len(hashes)}
