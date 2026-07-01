"""验证脚本：建库 + 插入测试数据 + 查询，确认所有 ORM 模型正确。

运行：.venv\Scripts\python.exe scripts\verify_models.py
"""

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# 让 backend 目录可被导入
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))

# 用临时库，验证完不污染正式库（先确保 data 目录存在）
_data_dir = os.path.join(PROJECT_ROOT, "data")
os.makedirs(_data_dir, exist_ok=True)
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_data_dir, "verify_test.db")

from datetime import datetime  # noqa: E402

from sqlalchemy import inspect, text  # noqa: E402
from database import Base, SessionLocal, engine  # noqa: E402
from models import Task, ListSource, Actor, Ranking, Setting, CrawlLog  # noqa: E402


def main() -> None:
    print("=== 1. 建表（Base.metadata.create_all）===")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    tables = sorted(inspector.get_table_names())
    print(f"已建表({len(tables)}): {tables}")
    expected = {"tasks", "list_sources", "actors", "actor_movies", "rankings", "settings", "crawl_logs"}
    missing = expected - set(tables)
    assert not missing, f"缺少表: {missing}"
    print("OK: 全部 7 张表已创建\n")

    print("=== 2. 插入测试数据 ===")
    with SessionLocal() as db:
        # ListSource
        src = ListSource(list_code="SDMM", list_path="/video_codes/SDMM")
        db.add(src)
        db.flush()  # 拿到 id
        print(f"ListSource: id={src.id} code={src.list_code}")

        # Task（含富元数据 + 观看状态）
        t = Task(
            list_source_id=src.id,
            url="/v/TEST-001",
            video_code="TEST-001",
            title="测试影片",
            actors="演员A,演员B",
            rating=8.5,
            view_status="viewed",
        )
        db.add(t)
        db.flush()
        print(f"Task: id={t.id} code={t.video_code} status={t.status} view={t.view_status}")

        # Actor（带关注）
        a = Actor(name="演员A", is_followed=True, movie_count=100)
        db.add(a)
        db.flush()
        print(f"Actor: id={a.id} name={a.name} followed={a.is_followed}")

        # Actor-Movie 关联
        a.tasks.append(t)
        print(f"Actor-Movie 关联: {a.name} <-> {t.video_code}")

        # Ranking
        r = Ranking(
            rank_type="weekly", rank_date="2026-07-01", rank_position=1,
            video_code="TEST-001", task_id=t.id,
        )
        db.add(r)
        db.flush()
        print(f"Ranking: id={r.id} type={r.rank_type} pos={r.rank_position}")

        # Setting
        db.add(Setting(key="javdb_url", value="https://javdb.com"))
        db.flush()
        print("Setting: javdb_url=https://javdb.com")

        # CrawlLog
        db.add(CrawlLog(list_source_id=src.id, crawl_type="scan", message="扫描完成"))
        db.commit()
        print("提交成功\n")

    print("=== 3. 查询验证 ===")
    with SessionLocal() as db:
        # 统计
        counts = {}
        for model, name in [(Task, "tasks"), (ListSource, "list_sources"), (Actor, "actors"),
                            (Ranking, "rankings"), (Setting, "settings"), (CrawlLog, "crawl_logs")]:
            counts[name] = db.query(model).count()
        print(f"各表行数: {counts}")
        assert counts == {"tasks": 1, "list_sources": 1, "actors": 1, "rankings": 1,
                          "settings": 1, "crawl_logs": 1}, "行数不符"
        print("OK: 行数全部正确")

        # 关系验证：通过 actor 查到 task
        actor = db.query(Actor).first()
        linked_tasks = actor.tasks
        print(f"关系验证: {actor.name} 关联作品数={len(linked_tasks)}, 首个={linked_tasks[0].video_code}")
        assert len(linked_tasks) == 1 and linked_tasks[0].video_code == "TEST-001"
        print("OK: 多对多关系正确")

        # 反向关系：task.actor_refs
        task = db.query(Task).first()
        print(f"反向关系: {task.video_code} 关联演员={[a.name for a in task.actor_refs]}")
        assert "演员A" in [a.name for a in task.actor_refs]
        print("OK: 反向关系正确")

        # 观看状态筛选
        viewed = db.query(Task).filter(Task.view_status == "viewed").count()
        print(f"观看状态筛选: viewed 数量={viewed}")
        assert viewed == 1
        print("OK: view_status 筛选正确")

        # SQLite PRAGMA 验证
        with engine.connect() as conn:
            wal = conn.execute(text("PRAGMA journal_mode")).scalar()
            fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
            print(f"PRAGMA: journal_mode={wal} foreign_keys={fk}")
            assert str(wal).lower() == "wal"
            assert fk == 1
            print("OK: WAL + 外键已启用")

    print("\n✅ 全部验证通过！ORM 模型、关系、PRAGMA 均正确。")


if __name__ == "__main__":
    main()
