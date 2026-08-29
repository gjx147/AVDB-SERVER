"""Top250 数据层——jinjier.sqlite3 直读（不转存）+ 手动磁力导入 + 实时入库联查。"""
import re
import sqlite3
import threading
import zipfile
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from database import engine
from deps import CurrentUser, DbSession
from models import Task, Top250Magnet
from models.top250 import Top250Entry  # 旧表（一次性磁力迁移用）

router = APIRouter(prefix="/api/top250", tags=["top250"])

KIND_LABELS = {6: "JavDB TOP250", 7: "JavDB 有码 TOP250", 8: "JavDB 无码 TOP250",
               9: "JavDB 欧美 TOP250", 10: "JavDB FC2 TOP250"}
VALID_KINDS = (*KIND_LABELS.keys(), *range(2008, 2026))
RANKS_DB = Path("data/jinjier_ranks.db")
_pkg_lock = threading.Lock()


def _db_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "jinjier_ranks.db"


def _table_ensure() -> None:
    """主库建表（top250_magnets）+ 旧 top250_entries 磁力一次性迁移。"""
    Top250Magnet.__table__.create(bind=engine, checkfirst=True)
    # 旧表磁力迁移（仅一次：旧表有磁力且新表为空时）
    try:
        with engine.connect() as conn:
            cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(top250_entries)")}
            if "magnet" not in cols:
                return
        from database import SessionLocal
        odb = SessionLocal()
        try:
            old_rows = odb.execute(select(Top250Entry).where(Top250Entry.magnet.is_not(None))).scalars().all()
            if not old_rows:
                return
            have = {(m.kind, m.number) for m in odb.execute(select(Top250Magnet)).scalars().all()}
            moved = 0
            for e in old_rows:
                if (e.kind, e.number) in have:
                    continue
                odb.add(Top250Magnet(kind=e.kind, number=e.number, magnet=e.magnet,
                                     magnet_version=e.magnet_version, updated_at=e.updated_at))
                moved += 1
            odb.commit()
            if moved:
                print(f"[top250] 旧磁力迁移 {moved} 条")
        finally:
            odb.close()
    except Exception:
        pass


def _ranks_db() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "jinjier_ranks.db"


def _pkg_valid(db: Path) -> bool:
    try:
        conn = sqlite3.connect(str(db))
        row = conn.execute("SELECT COUNT(*) FROM ranks").fetchone()
        conn.close()
        return bool(row and row[0] > 0)
    except Exception:
        try:
            db.unlink()
        except Exception:
            pass
        return False


def _latest_pkg_url() -> str | None:
    try:
        r = httpx.get("https://jinjier.art/sql", timeout=20, follow_redirects=True)
        hits = re.findall(r'["\']([^"\']*?(\d{8})\.gif)["\']', r.text)
        if hits:
            best = max(hits, key=lambda h: int(h[1]))
            path = best[0]
            return path if path.startswith("http") else "https://jinjier.art/" + path.lstrip("/")
    except Exception:
        pass
    return None


def _ensure_pkg(force: bool = False) -> Path:
    """下载数据包（动态解析最新版本）原子落盘 + 损坏自愈。"""
    with _pkg_lock:
        db = _ranks_db()
        if db.exists() and not force and _pkg_valid(db):
            return db
        candidates = []
        latest = _latest_pkg_url()
        if latest:
            candidates.append(latest)
        for c in ["https://jinjier.art/20260112.gif", "https://jinjier.art/sql/20260112.gif"]:
            if c not in candidates:
                candidates.append(c)
        last = None
        for url in candidates:
            try:
                r = httpx.get(url, timeout=90, follow_redirects=True)
                if r.status_code != 200 or len(r.content) < 100000:
                    last = f"{url} -> HTTP {r.status_code}"
                    continue
                tmp_zip = db.with_suffix(".downloading")
                tmp_zip.write_bytes(r.content)
                with zipfile.ZipFile(tmp_zip) as z:
                    name = [n for n in z.namelist() if n.endswith(".sqlite3")][0]
                    z.extract(name, db.parent)
                tmp_name = db.with_suffix(".tmp")
                (db.parent / name).rename(tmp_name)
                tmp_name.replace(db)
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
    if re.search(r"-c\.", low) or low.endswith("-c"):
        return "-C"
    if re.search(r"-u\.", low) or low.endswith("-u"):
        return "-U"
    if "-bd" in low:
        return "-BD"
    if ".mp4" in low:
        return "MP4"
    if ".rar" in low:
        return "RAR"
    return "-"


class QueryBody(BaseModel := __import__("pydantic").BaseModel):
    __annotations__ = {"kind": int, "kind_end": int | None, "force": bool}
    kind: int = 6
    kind_end: int | None = None
    force: bool = False


@router.post("/query")
def query_kind(body: QueryBody, db: DbSession, _user: CurrentUser):
    """刷新数据包（下载最新快照替换本地 jinjier_ranks.db）并返回统计。"""
    if body.kind not in VALID_KINDS:
        raise HTTPException(status_code=400, detail=f"无效 kind: {body.kind}")
    _table_ensure()
    dbf = _ensure_pkg(force=body.force)
    conn = sqlite3.connect(str(dbf))
    kinds = [body.kind]
    if body.kind_end is not None:
        kinds = list(range(body.kind, body.kind_end + 1))
    summary = []
    grand = 0
    snapshot = None
    for k in kinds:
        rows = conn.execute(
            "SELECT COUNT(*), MAX(date) FROM ranks WHERE kind=?", (k,)).fetchone()
        snapshot = max(snapshot or "", rows[1] or "")
        grand += rows[0]
        summary.append({"kind": k, "label": KIND_LABELS.get(k, f"JavDB {k} TOP250"),
                        "total": rows[0], "snapshot": rows[1]})
    conn.close()
    return {"ok": True, "kinds": kinds, "grand_total": grand, "snapshot": snapshot,
            "summary": summary, "label": KIND_LABELS.get(body.kind, f"JavDB {body.kind} TOP250")}


@router.get("/list")
def list_entries(db: DbSession, _user: CurrentUser, kind: int = 6, q: str = "",
                 status: str = "all"):
    """榜单列表：ranks 直读 + 手动磁力 join + 实时入库联查。"""
    _table_ensure()
    dbf = _ranks_db()
    if not dbf.exists():
        raise HTTPException(status_code=409, detail="TOP250 数据尚未加载——先点「查询数据源」")
    if not _pkg_valid(dbf):
        raise HTTPException(status_code=409, detail="TOP250 数据文件损坏——点「查询数据源」自动重下")
    conn = sqlite3.connect(str(dbf))
    rows = conn.execute(
        "SELECT number, name, date, note, icon_url FROM ranks WHERE kind=? ORDER BY number",
        (kind,)).fetchall()
    conn.close()

    # 手动导入磁力
    mag = {m.number: m for m in db.execute(
        select(Top250Magnet).where(Top250Magnet.kind == kind)).scalars().all()}

    # 番号提取 + 入库联查（批量）
    parsed = []
    for rank, name, date, note, icon in rows:
        code = _extract_number(name)
        parsed.append({"rank": rank, "number": code or f"?(rank{rank})", "name": name,
                       "date": date or "", "icon": icon or ""})
    codes = [p["number"] for p in parsed if not p["number"].startswith("?")]
    tmap = {}
    if codes:
        for t in db.execute(select(Task).where(Task.video_code.in_(codes))).scalars().all():
            tmap[t.video_code] = t

    out = []
    snapshot = max((p["date"] for p in parsed), default=None)
    for p in parsed:
        num = p["number"]
        t = tmap.get(num)
        m = mag.get(num)
        mv = m.magnet_version if m else None
        in_lib = t is not None
        if q and q.upper() not in num.upper() and q not in p["name"]:
            continue
        if status == "in" and not in_lib:
            continue
        if status == "missing" and in_lib:
            continue
        if status == "nomagnet" and not (m or (in_lib and t and t.best_magnet)):
            continue
        out.append({"id": hash((kind, num)) & 0x7FFFFFFF, "kind": kind, "rank": p["rank"],
                    "number": num, "name": p["name"], "date": p["date"],
                    "poster_url": p["icon"], "magnet_version": mv,
                    "task_id": t.id if t else None, "in_library": in_lib,
                    "updated_at": p["date"], "prev_rank": None, "prev_date": None})
    return {"ok": True, "kind": kind, "snapshot": snapshot,
            "label": KIND_LABELS.get(kind, f"JavDB {kind} TOP250"), "total": len(out), "items": out}


class ImportBody(BaseModel):
    __annotations__ = {"kind": int}


@router.post("/import")
def import_files(db: DbSession, _user: CurrentUser, kind: int = 6,
                 csv_file: UploadFile = File(...), magnet_file: UploadFile = File(...)):
    """手动导入：磁力写入 top250_magnets（csv 仅校验统计）。"""
    if kind not in KIND_LABELS:
        raise HTTPException(status_code=400, detail="手动导入仅支持类型榜 kind=6~10")
    _table_ensure()
    LIMIT = 5 * 1024 * 1024
    mag_bytes = magnet_file.file.read()
    if len(mag_bytes) > LIMIT:
        raise HTTPException(status_code=413, detail="文件超过 5MB 上限")
    magnet_text = mag_bytes.decode("utf-8-sig", errors="replace")
    csv_text = csv_file.file.read().decode("utf-8-sig", errors="replace")

    stats = {"magnet_rows": 0, "magnet_imported": 0, "csv_rows": 0}
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
        existing = db.execute(select(Top250Magnet).where(
            Top250Magnet.kind == kind, Top250Magnet.number == code)).scalar_one_or_none()
        ver = _version_of(dn)
        if existing:
            existing.magnet, existing.magnet_version = line, ver
            existing.updated_at = datetime.now().strftime("%Y-%m-%d")
        else:
            db.add(Top250Magnet(kind=kind, number=code, magnet=line, magnet_version=ver,
                                updated_at=datetime.now().strftime("%Y-%m-%d")))
        stats["magnet_imported"] += 1
    stats["csv_rows"] = sum(1 for l in csv_text.splitlines() if l.strip())
    db.commit()
    return {"ok": True, "kind": kind, "label": KIND_LABELS.get(kind), **stats}


class CrawlBody(BaseModel):
    __annotations__ = {"kind": int}
    kind: int = 6


@router.post("/crawl-missing")
def crawl_missing(body: CrawlBody, db: DbSession, _user: CurrentUser):
    """批量入库：该 kind 下未入库番号交给爬虫 search-movie。"""
    from routers.crawl import _start_scraper_guarded
    from services.scraper_lock import is_running
    if is_running():
        raise HTTPException(status_code=409, detail="已有爬取任务在运行")
    _table_ensure()
    dbf = _ranks_db()
    if not dbf.exists():
        raise HTTPException(status_code=409, detail="TOP250 数据尚未加载——先点「查询数据源」")
    conn = sqlite3.connect(str(dbf))
    rows = conn.execute(
        "SELECT number, name, date, note, icon_url FROM ranks WHERE kind=? ORDER BY number",
        (body.kind,)).fetchall()
    conn.close()
    parsed = []
    for rank, name, date, note, icon in rows:
        code = _extract_number(name)
        if code:
            parsed.append({"rank": rank, "number": code, "name": name, "date": date or "",
                           "icon": icon or ""})
    codes = [p["number"] for p in parsed
             if not db.execute(select(Task).where(Task.video_code == p["number"]))
             .scalars().first()]
    if not codes:
        return {"ok": True, "queued": 0, "message": "没有未入库条目"}
    scraper = Path(__file__).resolve().parent.parent / "magnet_scraper" / "scraper.py"
    cmd = [str(scraper), "search-movie", "--codes", ",".join(codes), "--kind", str(body.kind)]
    proc = _start_scraper_guarded(cmd, {"mode": "search-movie", "kind": body.kind,
                                        "count": len(codes)})
    return {"ok": True, "queued": len(codes), "pid": proc.pid,
            "message": f"已启动番号搜索入库（{len(codes)} 部）"}


@router.post("/{number}/add-task")
def add_task(number: str, db: DbSession, _user: CurrentUser, kind: int = 6):
    """单部入库（按番号）。"""
    from routers.crawl import _start_scraper_guarded
    from services.scraper_lock import is_running
    if is_running():
        raise HTTPException(status_code=409, detail="已有爬取任务在运行")
    number = number.upper().replace(" ", "")
    if db.execute(select(Task).where(Task.video_code == number)).scalars().first():
        return {"ok": True, "message": "已在影片库"}
    scraper = Path(__file__).resolve().parent.parent / "magnet_scraper" / "scraper.py"
    cmd = [str(scraper), "search-movie", "--codes", number, "--kind", str(kind)]
    proc = _start_scraper_guarded(cmd, {"mode": "search-movie", "kind": kind, "count": 1})
    return {"ok": True, "message": f"已启动 {number} 的搜索入库（PID {proc.pid}）"}
