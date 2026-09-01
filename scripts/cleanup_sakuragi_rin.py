# -*- coding: utf-8 -*-
"""清理桜木凛(actor_id=442)误配数据。

用法（部署机容器内执行）:
    docker exec -it avdb-server python /app/scripts/cleanup_sakuragi_rin.py

做什么:
  1. 备份数据库到 /app/data/backups/cleanup_442_<ts>.db
  2. 删除 actor_movies 中 actor_id=442 的全部关联（桜庭ひかり的 142 部作品错挂）
  3. 重置 actor 442 的元数据（avatar_url/height/cup/measurements/debut_date/
     movie_count/works_fetched/source_url——全部是被桜庭ひかり污染的值）
  4. 删除这批误爬产生的失败任务（list_source_id=14 且 status='failed'，
     error_message 含"演员不匹配"）
  5. 删除误爬占用的 list_source 14（让订阅重跑时能重新建任务）
  6. 复核打印，全部在一个事务里，失败自动回滚

安全设计:
  - 预检: actor 442 名字必须是"桜木凛"，不是则中止（防止 id 被复用的库跑错）
  - 全程单事务，任何一步异常整体回滚
  - 删除前打印每步影响行数，删除后复核归零/重置确认
"""
import os
import shutil
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

DB = os.environ.get('AVDB_DB', '/app/data/javdb.db')
ACTOR_ID = 442
ACTOR_NAME = '桜木凛'
BAD_LIST_SOURCE = 14  # 误爬爬取源（crawl-actor 建的）

print(f'目标库: {DB}')
if not os.path.exists(DB):
    print(f'错误: 数据库不存在 {DB}')
    sys.exit(1)

# ── 0. 备份 ──
os.makedirs(os.path.join(os.path.dirname(DB), 'backups'), exist_ok=True)
ts = datetime.now().strftime('%Y%m%d_%H%M%S')
bak = os.path.join(os.path.dirname(DB), 'backups', f'cleanup_442_{ts}.db')
shutil.copy2(DB, bak)
print(f'[备份] {bak} ({os.path.getsize(bak)} bytes)')

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
conn.isolation_level = None  # 手动事务
cur = conn.cursor()

try:
    cur.execute('BEGIN IMMEDIATE')

    # ── 1. 预检 ──
    row = cur.execute('SELECT id, name, source_url, movie_count FROM actors WHERE id=?',
                      (ACTOR_ID,)).fetchone()
    if not row:
        print(f'[中止] 库里没有 actor_id={ACTOR_ID}，这个库可能没有误配数据（或已清理过）')
        conn.rollback()
        sys.exit(0)
    if row['name'] != ACTOR_NAME:
        print(f'[中止] actor_id={ACTOR_ID} 名字是 "{row["name"]}"（预期 "{ACTOR_NAME}"），'
              f'id 可能被复用，拒绝清理。请人工核对。')
        conn.rollback()
        sys.exit(1)
    print(f'[预检通过] actor_id={ACTOR_ID} = {row["name"]}，'
          f'当前 source_url={row["source_url"]}, movie_count={row["movie_count"]}')

    # ── 2. 删除错误关联 ──
    cur.execute('DELETE FROM actor_movies WHERE actor_id=?', (ACTOR_ID,))
    links_removed = cur.rowcount
    print(f'[清理1] 删除 actor_movies 关联: {links_removed} 行')

    # ── 3. 重置 442 元数据 ──
    cur.execute(
        """UPDATE actors SET
             avatar_url=NULL, avatar_local=NULL, gender=NULL, birth_date=NULL,
             height=NULL, cup=NULL, measurements=NULL, debut_date=NULL,
             movie_count=0, works_fetched=0, source_url=NULL,
             updated_at=datetime('now')
           WHERE id=?""",
        (ACTOR_ID,),
    )
    print(f'[清理2] 重置 actor {ACTOR_ID} 元数据: {cur.rowcount} 行')

    # ── 4. 删除误爬失败任务（list_source 14 且演员不匹配失败） ──
    cur.execute(
        """DELETE FROM tasks WHERE list_source_id=? AND status='failed'
           AND (error_message LIKE '%演员不匹配%' OR error_message LIKE '%不匹配%')""",
        (BAD_LIST_SOURCE,),
    )
    tasks_removed = cur.rowcount
    print(f'[清理3] 删除误爬失败任务: {tasks_removed} 行')

    # 残余检查: list_source 14 还有别的状态的任务吗
    remain = cur.execute(
        'SELECT status, COUNT(*) c FROM tasks WHERE list_source_id=? GROUP BY status',
        (BAD_LIST_SOURCE,),
    ).fetchall()
    remain_desc = {r['status']: r['c'] for r in remain} if remain else {}
    print(f'[检查] list_source={BAD_LIST_SOURCE} 残余任务: {remain_desc or "无"}')

    # ── 5. 删除误爬 list_source ──
    cur.execute('DELETE FROM list_sources WHERE id=?', (BAD_LIST_SOURCE,))
    print(f'[清理4] 删除 list_source {BAD_LIST_SOURCE}: {cur.rowcount} 行')

    # ── 6. 复核 ──
    n_links = cur.execute('SELECT COUNT(*) c FROM actor_movies WHERE actor_id=?',
                          (ACTOR_ID,)).fetchone()['c']
    a = cur.execute('SELECT name, movie_count, works_fetched, source_url, avatar_url '
                    'FROM actors WHERE id=?', (ACTOR_ID,)).fetchone()
    ok = (n_links == 0 and a['movie_count'] == 0 and a['works_fetched'] == 0
          and not a['source_url'] and not a['avatar_url'])
    print(f'[复核] 442 关联数={n_links}（预期 0）, movie_count={a["movie_count"]}（预期 0）, '
          f'works_fetched={a["works_fetched"]}（预期 0）, source_url={a["source_url"]!r}（预期空）')
    if not ok:
        raise RuntimeError('复核未通过，回滚')

    conn.commit()
    print()
    print('=' * 56)
    print(f'清理完成 ✓  关联-{links_removed} | 失败任务-{tasks_removed} | list_source-{BAD_LIST_SOURCE} 删除')
    print(f'备份: {bak}')
    print('下一步: 重跑桜木凛订阅，修复版会精确匹配到桜木凛本人。')
    print('=' * 56)
except Exception as e:
    conn.rollback()
    print(f'[回滚] 清理失败: {e}')
    sys.exit(1)
finally:
    conn.close()
