# -*- coding: utf-8 -*-
"""演员合并端点 + 配套改造的回归测试。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

import pytest

from conftest import client, db  # noqa: F401,E402

from models import Actor, ListSource, NewRelease, Subscription, Task, actor_movies
from database import SessionLocal


def _ensure_ls():
    s = SessionLocal()
    ls = s.query(ListSource).filter(ListSource.list_code == 'TEST').first()
    if not ls:
        ls = ListSource(list_code='TEST', list_path='/test')
        s.add(ls)
        s.commit()
        s.refresh(ls)
    lid = ls.id
    s.close()
    return lid


def _mk_actor(name, name_en=None):
    s = SessionLocal()
    a = Actor(name=name, name_en=name_en)
    s.add(a)
    s.commit()
    s.refresh(a)
    aid = a.id
    s.close()
    return aid


def _mk_task(code, actors_text=''):
    s = SessionLocal()
    t = Task(video_code=code, actors=actors_text, list_source_id=_ensure_ls(),
             url=f'https://test.example/{code}')
    s.add(t)
    s.commit()
    s.refresh(t)
    tid = t.id
    s.close()
    return tid


def _link(actor_id, task_id):
    s = SessionLocal()
    s.execute(actor_movies.insert().values(actor_id=actor_id, task_id=task_id))
    s.commit()
    s.close()


def test_merge_movies_subs_releases_aliases(client):
    keep = _mk_actor('樱木凛')
    dup = _mk_actor('桜木凛', name_en='Sakuragi Rin')
    t1 = _mk_task('CODE-001')
    t2 = _mk_task('CODE-002')
    t3 = _mk_task('CODE-003')
    _link(keep, t1)          # 主档案已有
    _link(dup, t1)           # 与主档案重复 -> 应删除而非双行
    _link(dup, t2)           # 迁移
    _link(dup, t3)           # 迁移
    # 订阅：主档案无、重复档案有 -> 转移
    s = SessionLocal()
    s.add(Subscription(name='桜木凛', sub_type='actor', actor_id=dup, auto_add=True))
    # 新作发现：重复档案两条（一条与主档案 video_code 重复 -> 丢弃，一条迁移）
    s.add(NewRelease(actor_id=keep, video_code='CODE-100'))
    s.add(NewRelease(actor_id=dup, video_code='CODE-100'))
    s.add(NewRelease(actor_id=dup, video_code='CODE-200'))
    s.commit()
    s.close()

    r = client.post('/api/actors/merge', json={'keep_id': keep, 'source_ids': [dup]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data['ok'] is True
    assert data['moved_movies'] == 2
    assert data['moved_subs'] == 1
    assert '桜木凛' in data['aliases_added']

    s = SessionLocal()
    links = s.execute(actor_movies.select().where(actor_movies.c.actor_id == keep)).fetchall()
    assert sorted(l.task_id for l in links) == sorted([t1, t2, t3])
    assert s.execute(actor_movies.select().where(actor_movies.c.actor_id == dup)).fetchall() == []
    assert s.query(Subscription).filter(Subscription.actor_id == dup).count() == 0
    nrs = s.query(NewRelease).filter(NewRelease.actor_id == keep).all()
    assert sorted(n.video_code for n in nrs) == ['CODE-100', 'CODE-200']
    assert s.query(NewRelease).filter(NewRelease.actor_id == dup).count() == 0
    assert s.get(Actor, dup) is None
    kept = s.get(Actor, keep)
    assert '桜木凛' in (kept.alias or '')
    sub = s.query(Subscription).filter(Subscription.actor_id == keep).first()
    assert sub is not None and sub.auto_add is True
    s.close()


def test_merge_both_subscribed_or_autoadd(client):
    keep = _mk_actor('主档案')
    dup = _mk_actor('重复档案')
    s = SessionLocal()
    s.add(Subscription(name='主档案', sub_type='actor', actor_id=keep, auto_add=False))
    s.add(Subscription(name='重复档案', sub_type='actor', actor_id=dup, auto_add=True))
    s.commit()
    s.close()
    r = client.post('/api/actors/merge', json={'keep_id': keep, 'source_ids': [dup]})
    assert r.status_code == 200, r.text
    s = SessionLocal()
    subs = s.query(Subscription).filter(Subscription.actor_id.in_([keep, dup])).all()
    assert len(subs) == 1
    assert subs[0].actor_id == keep and subs[0].auto_add is True  # OR 合并
    assert s.get(Actor, dup) is None
    s.close()


def test_merge_validation(client):
    a1 = _mk_actor('甲')
    r = client.post('/api/actors/merge', json={'keep_id': a1, 'source_ids': [a1]})
    assert r.status_code == 400
    r = client.post('/api/actors/merge', json={'keep_id': a1, 'source_ids': []})
    assert r.status_code == 400
    r = client.post('/api/actors/merge', json={'keep_id': a1, 'source_ids': [99999]})
    assert r.status_code == 404


def test_store_upsert_alias_dedup():
    """爬虫判重：旧名命中主档案 alias 时不新建行，返回主档案 id。"""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'magnet_scraper'))
    import tempfile
    import store as store_mod
    db_path = os.path.join(tempfile.gettempdir(), 'avdb_store_alias_test.db')
    if os.path.exists(db_path):
        os.remove(db_path)
    st = store_mod.SqliteTaskStore(db_path)
    main_id = st.upsert_actor('樱木凛', gender='female')
    with st._conn() as conn:
        conn.execute("UPDATE actors SET alias=? WHERE id=?", ('桜木凛', main_id))
        conn.commit()
    again = st.upsert_actor('桜木凛', gender='female')  # 旧名再被爬到
    assert again == main_id, f'应归并到主档案 {main_id}，实际 {again}'
    with st._conn() as conn:
        cnt = conn.execute("SELECT COUNT(*) FROM actors").fetchone()[0]
    assert cnt == 1, f'不应新建行，实际 {cnt} 行'


def test_cast_alias_fallback(client):
    """cast 匹配：task.actors 里的旧名通过 alias 找回主档案。"""
    keep = _mk_actor('樱木凛Cast测试')
    s = SessionLocal()
    a = s.get(Actor, keep)
    a.alias = '桜木凛Cast测试'
    s.commit()
    tid = _mk_task('CODE-CAST', actors_text='桜木凛Cast测试, 无此人')
    s.close()
    r = client.get(f'/api/tasks/{tid}/cast')
    assert r.status_code == 200, r.text
    items = r.json()
    # 命中 alias 后返回主档案名（樱木凛Cast测试）；未匹配的保持原名 id=None
    assert any(i['id'] == keep and i['name'] == '樱木凛Cast测试' for i in items)
    assert any(i['id'] is None and i['name'] == '无此人' for i in items)


def test_search_alias(client):
    keep = _mk_actor('樱木凛')
    s = SessionLocal()
    s.get(Actor, keep).alias = '桜木凛'
    s.commit()
    s.close()
    r = client.get('/api/actors', params={'q': '桜木凛'})
    assert r.status_code == 200
    d = r.json()
    items = d['items'] if isinstance(d, dict) else d
    assert any(a['id'] == keep for a in items)
def test_merge_multi_source_both_subscribed(client):
    """A1 回归：keep 无订阅 + 两个 source 各有订阅（autoflush=False 下曾撞唯一约束）。"""
    keep = _mk_actor('保留主档')
    d1 = _mk_actor('重复甲')
    d2 = _mk_actor('重复乙')
    s = SessionLocal()
    s.add(Subscription(name='重复甲', sub_type='actor', actor_id=d1, auto_add=True))
    s.add(Subscription(name='重复乙', sub_type='actor', actor_id=d2, auto_add=False))
    s.commit()
    s.close()
    r = client.post('/api/actors/merge', json={'keep_id': keep, 'source_ids': [d1, d2]})
    assert r.status_code == 200, r.text
    s = SessionLocal()
    subs = s.query(Subscription).filter(Subscription.actor_id.in_([keep, d1, d2])).all()
    assert len(subs) == 1
    assert subs[0].actor_id == keep and subs[0].auto_add is True  # 第一个转移 + 第二个 OR 合并
    assert s.get(Actor, d1) is None and s.get(Actor, d2) is None
    s.close()


def test_store_upsert_alias_token_exact():
    """alias token 级判重：精确 token 命中归并；子串不误归并。"""
    import tempfile
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'magnet_scraper'))
    import store as store_mod
    db_path = os.path.join(tempfile.gettempdir(), 'avdb_store_token_test.db')
    if os.path.exists(db_path):
        os.remove(db_path)
    st = store_mod.SqliteTaskStore(db_path)
    main_id = st.upsert_actor('JULIA')
    with st._conn() as conn:
        conn.execute("UPDATE actors SET alias=? WHERE id=?", ('JUL-1/JULIA', main_id))
        conn.commit()
    assert st.upsert_actor('JUL-1') == main_id  # token 命中 -> 归并
    juli = st.upsert_actor('JULI')              # 子串但非 token -> 新建，不误归并
    assert juli != main_id
    with st._conn() as conn:
        cnt = conn.execute("SELECT COUNT(*) FROM actors").fetchone()[0]
    assert cnt == 2


def test_store_upsert_alias_hit_skips_profile_fields():
    """E2：旧名经 alias 归并后再被爬到，不反写主档案 source_url/avatar_url/note。"""
    import tempfile
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'magnet_scraper'))
    import store as store_mod
    db_path = os.path.join(tempfile.gettempdir(), 'avdb_store_e2e_test.db')
    if os.path.exists(db_path):
        os.remove(db_path)
    st = store_mod.SqliteTaskStore(db_path)
    main_id = st.upsert_actor('A演员', source_url='https://x/A')
    with st._conn() as conn:
        conn.execute("UPDATE actors SET alias=? WHERE id=?", ('B演员', main_id))
        conn.commit()
    st.upsert_actor('B演员', source_url='https://x/B', avatar_url='https://x/b.jpg', note='source_url: https://x/B')
    with st._conn() as conn:
        row = conn.execute("SELECT source_url, avatar_url, note FROM actors WHERE id=?", (main_id,)).fetchone()
    assert row["source_url"] == 'https://x/A'
    assert row["avatar_url"] is None
    assert row["note"] is None