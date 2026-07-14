"""任务存储层（原生 sqlite3 直连，与 backend 共用同一 db 文件）。

scraper 作为独立子进程运行，无法使用 backend 的 SQLAlchemy session，
因此用原生 sqlite3 直接连接同一个 data/javdb.db（WAL 模式支持并发）。

支持全部富元数据字段（对齐 backend/models/task.py 的 Task 表）。
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import List, Optional


def get_db_path() -> Path:
    """获取数据库路径，优先 config.DB_PATH，否则项目根 data/javdb.db。"""
    try:
        import config as _config
        path = getattr(_config, "DB_PATH", None)
        if path is not None:
            path = Path(path)
        else:
            path = Path(__file__).resolve().parent.parent / "data" / "javdb.db"
    except Exception:
        path = Path(__file__).resolve().parent.parent / "data" / "javdb.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# --- 建表 SQL（与 backend SQLAlchemy 模型一致，scraper 侧兜底建表）---
INIT_SQL = """
CREATE TABLE IF NOT EXISTS list_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    list_code TEXT NOT NULL UNIQUE,
    list_path TEXT NOT NULL,
    list_params TEXT NOT NULL DEFAULT 'f=download',
    max_pages INTEGER NOT NULL DEFAULT 100,
    last_scanned_page INTEGER NOT NULL DEFAULT 0,
    last_scanned_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    list_source_id INTEGER NOT NULL,
    url TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    retry_count INTEGER NOT NULL DEFAULT 0,
    best_magnet TEXT,
    magnets_json TEXT,
    video_code TEXT,
    error_message TEXT,
    title TEXT,
    poster_url TEXT,
    thumbnail_urls TEXT,
    synopsis TEXT,
    description TEXT,
    actors TEXT,
    tags TEXT,
    release_date TEXT,
    duration TEXT,
    director TEXT,
    maker TEXT,
    label TEXT,
    series TEXT,
    rating REAL,
    file_size TEXT,
    is_favorite INTEGER NOT NULL DEFAULT 0,
    favorite_at TEXT,
    note TEXT,
    view_status TEXT,
    viewed_at TEXT,
    download_status TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (list_source_id) REFERENCES list_sources(id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_list_status ON tasks(list_source_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_video_code ON tasks(video_code);

CREATE TABLE IF NOT EXISTS actors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_en TEXT,
    avatar_url TEXT,
    avatar_local TEXT,
    gender TEXT,
    is_followed INTEGER NOT NULL DEFAULT 0,
    is_blacklisted INTEGER NOT NULL DEFAULT 0,
    movie_count INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS actor_movies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (actor_id) REFERENCES actors(id) ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rankings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rank_type TEXT NOT NULL,
    rank_date TEXT NOT NULL,
    rank_position INTEGER NOT NULL,
    video_code TEXT,
    title TEXT,
    cover_url TEXT,
    score REAL,
    views INTEGER,
    detail_url TEXT,
    task_id INTEGER,
    is_in_library INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_rankings_type_date ON rankings(rank_type, rank_date);
CREATE INDEX IF NOT EXISTS idx_rankings_video_code ON rankings(video_code);
"""


def _extract_magnet_hash(magnet: str) -> Optional[str]:
    """从磁力链提取 infohash（btih: 后的 40 位十六进制）。"""
    if not magnet:
        return None
    m = re.search(r"btih:([a-fA-F0-9]{40})", magnet)
    return m.group(1).lower() if m else None


class SqliteTaskStore:
    """基于原生 sqlite3 的任务存储。"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or get_db_path()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(INIT_SQL)

    # ---------- 列表源 ----------
    def ensure_list_source(self, list_code: str, list_path: str = None,
                           list_params: str = "f=download", max_pages: int = 100) -> dict:
        code = list_code.strip().upper()
        path = list_path or f"/video_codes/{code}"
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM list_sources WHERE list_code = ?", (code,))
            row = cur.fetchone()
            if row:
                return dict(row)
            conn.execute(
                "INSERT INTO list_sources (list_code, list_path, list_params, max_pages, last_scanned_page) VALUES (?,?,?,?,0)",
                (code, path, list_params, max_pages))
            conn.commit()
            return dict(conn.execute("SELECT * FROM list_sources WHERE list_code=?", (code,)).fetchone())

    def get_list_source_by_id(self, list_source_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM list_sources WHERE id=?", (list_source_id,)).fetchone()
            return dict(row) if row else None

    def set_last_scanned_page(self, list_source_id: int, page: int) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE list_sources SET last_scanned_page=? WHERE id=?", (page, list_source_id))
            conn.commit()

    def get_last_scanned_page(self, list_source_id: int) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT last_scanned_page FROM list_sources WHERE id=?", (list_source_id,)).fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    # ---------- 任务队列 ----------
    def get_pending_urls(self, list_source_id: int, limit: int = None) -> List[str]:
        with self._conn() as conn:
            sql = "SELECT url FROM tasks WHERE list_source_id=? AND status='pending' ORDER BY id"
            if limit:
                sql += f" LIMIT {int(limit)}"
            return [r[0] for r in conn.execute(sql, (list_source_id,)).fetchall()]

    def get_failed_urls(self, list_source_id: int, limit: int = None) -> List[str]:
        with self._conn() as conn:
            sql = "SELECT url FROM tasks WHERE list_source_id=? AND status='failed' ORDER BY id"
            if limit:
                sql += f" LIMIT {int(limit)}"
            return [r[0] for r in conn.execute(sql, (list_source_id,)).fetchall()]

    def add_pending_urls(self, list_source_id: int, urls: List[str]) -> int:
        if not urls:
            return 0
        added = 0
        with self._conn() as conn:
            for u in urls:
                try:
                    conn.execute(
                        "INSERT INTO tasks (list_source_id, url, status) VALUES (?,?, 'pending')",
                        (list_source_id, u.strip()))
                    added += 1
                except sqlite3.IntegrityError:
                    pass
            conn.commit()
        return added

    def get_visited_urls(self, list_source_id: int = None) -> set:
        with self._conn() as conn:
            if list_source_id is not None:
                cur = conn.execute(
                    "SELECT url FROM tasks WHERE list_source_id=? AND status='visited'", (list_source_id,))
            else:
                cur = conn.execute("SELECT url FROM tasks WHERE status='visited'")
            return {r[0] for r in cur.fetchall()}

    def get_task_by_url(self, url: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE url=?", (url,)).fetchone()
            return dict(row) if row else None

    def task_exists_with_url(self, url: str) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM tasks WHERE url=? LIMIT 1", (url,)).fetchone() is not None

    def update_task_url(self, old_url: str, new_url: str) -> bool:
        with self._conn() as conn:
            try:
                conn.execute("UPDATE tasks SET url=?, updated_at=datetime('now') WHERE url=?", (new_url, old_url))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def delete_task_by_url(self, url: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM tasks WHERE url=?", (url,))
            conn.commit()
            return cur.rowcount > 0

    # ---------- 磁力去重 ----------
    def is_magnet_duplicate(self, magnet: str) -> bool:
        """检查该磁力链是否已存在于任意任务的 magnets_json 中。"""
        h = _extract_magnet_hash(magnet)
        if not h:
            return False
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT 1 FROM tasks WHERE magnets_json LIKE ? LIMIT 1", (f"%{h}%",))
            return cur.fetchone() is not None

    # ---------- 结果写入 ----------
    def mark_visited(self, url: str, *, best_magnet: str = None,
                     magnets_json: str = None, video_code: str = None,
                     title: str = None, poster_url: str = None,
                     thumbnail_urls: str = None, synopsis: str = None,
                     actors: str = None, description: str = None, tags: str = None,
                     release_date: str = None, duration: str = None,
                     director: str = None, maker: str = None, label: str = None,
                     series: str = None, rating: float = None, file_size: str = None) -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE tasks SET
                    status='visited', best_magnet=?, magnets_json=?, video_code=?,
                    title=?, poster_url=?, thumbnail_urls=?, synopsis=?, description=?,
                    actors=?, tags=?, release_date=?, duration=?, director=?, maker=?,
                    label=?, series=?, rating=?, file_size=?,
                    updated_at=datetime('now')
                   WHERE url=?""",
                (best_magnet, magnets_json, video_code, title, poster_url, thumbnail_urls,
                 synopsis, description, actors, tags, release_date, duration, director,
                 maker, label, series, rating, file_size, url))
            conn.commit()

    def mark_failed(self, url: str, error_message: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE tasks SET status='failed', error_message=?,
                   retry_count=retry_count+1, updated_at=datetime('now') WHERE url=?""",
                (error_message[:500] if error_message else None, url))
            conn.commit()

    # ---------- 演员关联 ----------
    def upsert_actor(self, name: str, **fields) -> int:
        """新增或更新演员，返回 actor_id。"""
        with self._conn() as conn:
            row = conn.execute("SELECT id FROM actors WHERE name=?", (name,)).fetchone()
            if row:
                actor_id = row[0]
                sets = ", ".join(f"{k}=?" for k in fields)
                if sets:
                    conn.execute(f"UPDATE actors SET {sets}, updated_at=datetime('now') WHERE id=?",
                                 (*fields.values(), actor_id))
                    conn.commit()
                return actor_id
            cols = ["name"] + list(fields.keys())
            vals = [name] + list(fields.values())
            placeholders = ",".join("?" * len(cols))
            cur = conn.execute(
                f"INSERT INTO actors ({','.join(cols)}) VALUES ({placeholders})", vals)
            conn.commit()
            return cur.lastrowid

    def link_actor_movie(self, actor_id: int, task_id: int) -> None:
        """关联演员与作品（去重）。"""
        with self._conn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM actor_movies WHERE actor_id=? AND task_id=?", (actor_id, task_id)).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO actor_movies (actor_id, task_id) VALUES (?,?)", (actor_id, task_id))
                conn.commit()

    # ---------- 排行榜 ----------
    def save_rankings(self, entries: List[dict], rank_type: str,
                      rank_date: str = None) -> int:
        """批量保存排行榜条目（按 video_code+rank_type+rank_date 去重）。

        尝试匹配已有 task（按 video_code），命中则回填 task_id + is_in_library。
        返回实际插入条数。
        """
        if not entries:
            return 0
        from datetime import date
        rd = rank_date or date.today().isoformat()
        inserted = 0
        with self._conn() as conn:
            for e in entries:
                vc = (e.get("video_code") or "").strip()
                if not vc:
                    continue
                # 去重：同 type+date+video_code 已存在则跳过
                exists = conn.execute(
                    "SELECT 1 FROM rankings WHERE rank_type=? AND rank_date=? AND video_code=? LIMIT 1",
                    (rank_type, rd, vc)
                ).fetchone()
                if exists:
                    continue
                # 尝试匹配已有 task
                task_row = conn.execute(
                    "SELECT id FROM tasks WHERE video_code=? LIMIT 1", (vc,)
                ).fetchone()
                task_id = task_row[0] if task_row else None
                in_lib = 1 if task_id else 0
                conn.execute(
                    """INSERT INTO rankings
                       (rank_type, rank_date, rank_position, video_code, title,
                        cover_url, score, views, detail_url, task_id, is_in_library, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
                    (rank_type, rd, e.get("rank_position", 0), vc,
                     e.get("title"), e.get("cover_url"), e.get("score"),
                     e.get("views"), e.get("detail_url"), task_id, in_lib))
                inserted += 1
            conn.commit()
        return inserted

    def update_ranking_task_ids(self, matches: List[tuple]) -> int:
        """根据 detail_url 批量回填 rankings.task_id。

        matches: [(detail_url, task_id), ...]
        """
        if not matches:
            return 0
        updated = 0
        with self._conn() as conn:
            for detail_url, task_id in matches:
                cur = conn.execute(
                    "UPDATE rankings SET task_id=?, is_in_library=1 WHERE detail_url=?",
                    (task_id, detail_url))
                updated += cur.rowcount
            conn.commit()
        return updated
