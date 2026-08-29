"""Top250 独立模块——两种数据来源：jinjier.art 数据包查询 / 手动导入 csv+magnet。
入库：按番号自动爬取（scraper search-movie）→ 影片库与 Top250 页同时可见。"""
import re
import sqlite3
import zipfile
from pathlib import Path

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from database import Base, engine
from deps import CurrentUser, DbSession
from models import Task, Top250Entry

router = APIRouter(prefix="/api/top250", tags=["top250"])

KIND_LABELS = {6: "JavDB TOP250", 7: "JavDB 有码 TOP250", 8: "JavDB 无码 TOP250",
               9: "JavDB 欧美 TOP250", 10: "JavDB FC2 TOP250"}

PKG_CANDIDATES = ["https://jinjier.art/20260112.gif", "https://jinjier.art/sql/20260112.gif"]
VALID_KINDS = (*KIND_LABELS.keys(), *range(2008, 2026))


def _db_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "jinjier_ranks.db"


def _table_ensure() -> None:
    """Top250 独立表（项目无迁移体系，checkfirst 幂等建表）。"""
    Top250Entry.__table__.create(bind=engine, checkfirst=True)


def _ensure_pkg(force: bool = False) -> Path:
    """下载数据包（伪装 .gif 的 zip）并解压出 sqlite（幂等）。"""
    db = _db_path()
    if db.exists() and not force:
        return db
    last = None
    for url in PKG_CANDIDATES:
        try:
            r = httpx.get(url, timeout=90, follow_redirects=True)
            if r.status_code != 200 or len(r.content) < 100000:
                last = f"{url} -> HTTP {r.status_code} ({len(r.content)}B)"
                continue
            tmp_zip = db.with_suffix(".zip")
            tmp_zip.write_bytes(r.content)
            with zipfile.ZipFile(tmp_zip) as z:
                name = [n for n in z.namelist() if n.endswith(".sqlite3")][0]
                z.extract(name, db.parent)
                (db.parent / name).rename(db)
            tmp_zip.unlink(missing_ok=True)
            return db
        except Exception as e:
            last = f"{url} -> {e}"
    raise HTTPException(status_code=502, detail=f"jinjier 数据包下载失败：{last}")


def _extract_number(name: str) -> str:
    m = re.search(r"([A-Za-z]{2,6})-?(\d{2,6})", name or "")
    return f"{m.group(1).upper()}-{m.group(2)}" if m else ""


def _version_of(dn: str) -> str:
    low = dn.lower()
    if "-uc" in low:
        return "-UC"
    if re.search(r"-c\.", low) or "-c " in low or low.endswith("-c"):
        return "-C"
    if re.search(r"-u\.", low) or "-u " in low or low.endswith("-u"):
        return "-U"
    if "-bd" in low:
        return "-BD"
    if ".mp4" in low:
        return "MP4"
    if ".rar" in low:
        return "RAR"
    return "-"


def _upsert_entry(db: DbSession, kind: int, rank: int, number: str, name: str,
                  date: str | None, note: str | None, icon_url: str | None) -> Top250Entry:
    e = db.execute(select(Top250Entry).where(Top250Entry.kind == kind,
                                             Top250Entry.number == number)).scalar_one_or_none()
    if not e:
        e = Top250Entry(kind=kind, number=number)
        db.add(e)
    e.rank, e.name, e.date, e.note, e.icon_url = rank, name, date, note, icon_url
    return e


def _mark_in_library(db: DbSession, kind: int) -> int:
    """按番号匹配 tasks.video_code 回填 task_id。"""
    entries = db.execute(select(Top250Entry).where(Top250Entry.kind == kind,
                                                   Top250Entry.task_id.is_(None))).scalars().all()
    hit = 0
    for e in entries:
        t = db.execute(select(Task).where(Task.video_code == e.number)).scalars().first()
        if t:
            e.task_id = t.id
            hit += 1
    db.commit()
    return hit


class QueryBody(BaseModel):
    kind: int
    kind_end: int | None = None  # 年份范围批量查询：kind..kind_end 逐年入库
    force: bool = False


@router.post("/query")
def query_kind(body: QueryBody, db: DbSession, _user: CurrentUser):
    """从 jinjier 数据包查询 TOP250（kind 单个，或 kind..kind_end 年份范围批量），幂等入库。"""
    kinds = [body.kind]
    if body.kind_end is not None:
        if body.kind not in range(2008, 2026) or body.kind_end not in range(2008, 2026):
            raise HTTPException(status_code=400, detail="年份范围查询仅支持 2008~2025")
        if body.kind_end < body.kind:
            raise HTTPException(status_code=400, detail="结束年份不能早于起始年份")
        kinds = list(range(body.kind, body.kind_end + 1))
    elif body.kind not in VALID_KINDS:
        raise HTTPException(status_code=400, detail=f"无效 kind: {body.kind}")
    _table_ensure()
    dbf = _ensure_pkg(force=body.force)
    conn = sqlite3.connect(str(dbf))
    summary = []
    grand_total = 0
    for k in kinds:
        rows = conn.execute(
            "SELECT number, name, date, note, icon_url FROM ranks WHERE kind=? ORDER BY number",
            (k,)).fetchall()
        no_code = 0
        for rank, name, date, note, icon in rows:
            code = _extract_number(name)
            if not code:
                no_code += 1
                code = f"?(rank{rank})"
            _upsert_entry(db, k, rank, code, name, date, note, icon)
        db.commit()
        synced = _mark_in_library(db, k)
        grand_total += len(rows)
        summary.append({"kind": k, "label": KIND_LABELS.get(k, f"JavDB {k} TOP250"),
                        "total": len(rows), "no_code": no_code, "in_library_synced": synced})
    conn.close()
    return {"ok": True, "kinds": kinds, "grand_total": grand_total, "summary": summary,
            "label": KIND_LABELS.get(body.kind, f"JavDB {body.kind} TOP250")}


class ImportBody(BaseModel):
    kind: int = 6


@router.post("/import")
def import_files(db: DbSession, _user: CurrentUser, kind: int = 6,
                 csv_file: UploadFile = File(...), magnet_file: UploadFile = File(...)):
    """手动导入 top250-code.csv + top250-magnet.txt（幂等 upsert；仅类型榜 6~10）。"""
    if kind not in KIND_LABELS:
        raise HTTPException(status_code=400, detail="手动导入仅支持类型榜 kind=6~10")
    _table_ensure()
    csv_text = csv_file.file.read().decode("utf-8-sig", errors="replace")
    magnet_text = magnet_file.file.read().decode("utf-8-sig", errors="replace")

    stats = {"csv_rows": 0, "magnet_rows": 0, "magnet_matched": 0, "no_code": 0}
    for line in csv_text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2 or not parts[1]:
            continue
        rank = int(parts[0]) if parts[0].isdigit() else 0
        number = parts[1].upper().replace(" ", "")
        _upsert_entry(db, kind, rank, number, number,
                      parts[2] if len(parts) > 2 else None, KIND_LABELS.get(kind), None)
        stats["csv_rows"] += 1
    db.commit()

    entries = db.execute(select(Top250Entry).where(Top250Entry.kind == kind)).scalars().all()
    by_code = {e.number.upper(): e for e in entries}
    for line in magnet_text.splitlines():
        line = line.strip()
        if not line.startswith("magnet:"):
            continue
        stats["magnet_rows"] += 1
        dm = re.search(r"&dn=([^&\n]+)", line)
        dn = dm.group(1) if dm else ""
        cm = re.search(r"([A-Za-z]{2,6})-?(\d{2,6})", dn.replace(" ", ""))
        if not cm:
            continue
        code = f"{cm.group(1).upper()}-{cm.group(2)}"
        e = by_code.get(code)
        if not e:
            continue
        e.magnet = line
        e.magnet_version = _version_of(dn)
        stats["magnet_matched"] += 1
    db.commit()
    no_code = db.execute(select(Top250Entry).where(
        Top250Entry.kind == kind, Top250Entry.number.like("?%"))).scalars().all()
    stats["no_code"] = len(no_code)
    for e in no_code:
        db.delete(e)
    db.commit()
    stats["in_library_synced"] = _mark_in_library(db, kind)
    return {"ok": True, "kind": kind, "label": KIND_LABELS.get(kind), **stats}


@router.get("/list")
def list_entries(db: DbSession, _user: CurrentUser, kind: int = 6, q: str = "", status: str = "all"):
    """列表：rank/番号/标题/日期/磁力版本/入库状态。status=all|in|missing|nomagnet。"""
    _table_ensure()
    rows = db.execute(select(Top250Entry).where(Top250Entry.kind == kind)
                      .order_by(Top250Entry.rank)).scalars().all()
    out = []
    for e in rows:
        in_lib = e.task_id is not None
        if not in_lib and e.number and not e.number.startswith("?"):
            t = db.execute(select(Task).where(Task.video_code == e.number)).scalars().first()
            if t:
                e.task_id = t.id
                in_lib = True
        if q and q.upper() not in (e.number or "").upper() and q not in (e.name or ""):
            continue
        if status == "in" and not in_lib:
            continue
        if status == "missing" and in_lib:
            continue
        if status == "nomagnet" and e.magnet:
            continue
        out.append({"id": e.id, "kind": e.kind, "rank": e.rank, "number": e.number,
                    "name": e.name, "date": e.date, "magnet_version": e.magnet_version,
                    "poster_url": e.icon_url, "task_id": e.task_id, "in_library": in_lib})
    db.commit()
    return {"ok": True, "kind": kind,
            "label": KIND_LABELS.get(kind, f"JavDB {kind} TOP250"), "total": len(out), "items": out}


class CrawlBody(BaseModel):
    kind: int


def _launch_search_movie(codes: list[str], kind: int) -> int:
    from routers.crawl import _start_scraper_guarded
    from services.scraper_lock import is_running
    if is_running():
        raise HTTPException(status_code=409, detail="已有爬取任务在运行")
    scraper = Path(__file__).resolve().parent.parent / "magnet_scraper" / "scraper.py"
    cmd = [str(scraper), "search-movie", "--codes", ",".join(codes), "--kind", str(kind)]
    proc = _start_scraper_guarded(cmd, {"mode": "search-movie", "kind": kind, "count": len(codes)})
    return proc.pid


@router.post("/crawl-missing")
def crawl_missing(body: CrawlBody, db: DbSession, _user: CurrentUser):
    """批量入库：该 kind 未入库番号交给爬虫 search-movie（搜索详情页→建任务→提取）。"""
    rows = db.execute(select(Top250Entry).where(
        Top250Entry.kind == body.kind, Top250Entry.task_id.is_(None))).scalars().all()
    codes = [e.number for e in rows if e.number and not e.number.startswith("?")]
    if not codes:
        return {"ok": True, "queued": 0, "message": "没有未入库条目"}
    pid = _launch_search_movie(codes, body.kind)
    return {"ok": True, "queued": len(codes), "pid": pid,
            "message": f"已启动番号搜索入库（{len(codes)} 部），完成后自动回填入库状态"}


@router.post("/{entry_id}/add-task")
def add_task(entry_id: int, db: DbSession, _user: CurrentUser):
    """单部入库。"""
    e = db.get(Top250Entry, entry_id)
    if not e:
        raise HTTPException(status_code=404, detail="条目不存在")
    if e.number.startswith("?"):
        return {"ok": False, "message": "该条目无有效番号"}
    if e.task_id:
        return {"ok": True, "task_id": e.task_id, "message": "已在影片库"}
    pid = _launch_search_movie([e.number], e.kind)
    return {"ok": True, "message": f"已启动 {e.number} 的搜索入库（PID {pid}）"}
