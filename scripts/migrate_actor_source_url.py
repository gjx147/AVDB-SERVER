"""迁移脚本：把 actors.note 中遗留的 "source_url: <url>" 迁移到 actors.source_url 列（幂等）。

背景：历史版本把 JavDB 演员页链接存在 note 字段（"source_url: https://javdb.com/actors/xxx"），
后来新增独立 source_url 列但旧数据未迁移。本脚本一次性迁移，之后可不再依赖后端 fallback。

运行：.venv\\Scripts\\python.exe scripts\\migrate_actor_source_url.py
"""
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))

from sqlalchemy import text  # noqa: E402
from database import SessionLocal  # noqa: E402


def main() -> None:
    db = SessionLocal()
    try:
        # 幂等：只处理 source_url 为空且 note 带 "source_url: " 前缀的行
        rows = db.execute(
            text(
                "SELECT id, note FROM actors "
                "WHERE (source_url IS NULL OR source_url = '') AND note LIKE 'source_url: %'"
            )
        ).fetchall()
        updated = 0
        skipped = 0
        for actor_id, note in rows:
            url = (note or "")[len("source_url: "):].strip()
            if url:
                db.execute(
                    text("UPDATE actors SET source_url = :url WHERE id = :id"),
                    {"url": url, "id": actor_id},
                )
                updated += 1
            else:
                skipped += 1
        db.commit()
        print(f"迁移完成：{updated} 条已迁移，{skipped} 条 note 格式无效跳过（共 {len(rows)} 条候选）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
