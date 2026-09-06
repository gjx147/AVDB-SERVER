# -*- coding: utf-8 -*-
"""作品筛选（F1）+ 手动添加作品（F2）回归测试。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from conftest import client, db  # noqa: F401,E402

from models import Actor, ListSource, Task, actor_movies
from database import SessionLocal


def _mk_actor(name, gender=None):
    s = SessionLocal()
    a = Actor(name=name, gender=gender)
    s.add(a)
    s.commit()
    s.refresh(a)
    aid = a.id
    s.close()
    return aid


def _mk_task(code, magnet=None):
    magnet = 'MAG-' + code if magnet is None else magnet  # 传空串 = 无磁力（哨兵）
    s = SessionLocal()
    ls = s.query(ListSource).filter(ListSource.list_code == 'WF').first()
    if not ls:
        ls = ListSource(list_code='WF', list_path='/wf')
        s.add(ls)
        s.commit()
        s.refresh(ls)
    t = Task(video_code=code, url=f'https://javdb.com/v/{code}', list_source_id=ls.id,
             status='visited', best_magnet=magnet)
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


def test_movies_cast_filter(client):
    """F1：cast=solo/multi 按 actor_movies × gender 精确统计（男优不计、NULL 不计）。"""
    x = _mk_actor('WF-X', 'female')    # 主角（列表所属演员）
    y = _mk_actor('WF-Y', 'female')
    z = _mk_actor('WF-Z')              # gender NULL
    mm = _mk_actor('WF-M', 'male')     # 男优不计入
    t1 = _mk_task('WF-T1')             # 单体：只有 X
    t2 = _mk_task('WF-T2')             # 多人：X + Y
    t3 = _mk_task('WF-T3')             # 单体：X + 男优
    t4 = _mk_task('WF-T4')             # 多人：X + Y + NULL
    t5 = _mk_task('WF-T5', magnet='')  # 无磁力：任何筛选下都不出现
    for t in (t1, t2, t3, t4, t5):
        _link(x, t)
    _link(y, t2)
    _link(y, t4)
    _link(z, t4)
    _link(mm, t3)

    r = client.get(f'/api/actors/{x}/movies', params={'cast': 'all'})
    ids = {i['id'] for i in r.json()['items']}
    assert ids == {t1, t2, t3, t4}  # 无磁力 t5 永不出现

    r = client.get(f'/api/actors/{x}/movies', params={'cast': 'solo'})
    assert {i['id'] for i in r.json()['items']} == {t1, t3}  # 男优不计 -> t3 仍单体

    r = client.get(f'/api/actors/{x}/movies', params={'cast': 'multi'})
    assert {i['id'] for i in r.json()['items']} == {t2, t4}  # NULL gender 不计入 -> t4 仍多人


def test_add_work_existing_task_link_only(client):
    """F2：URL 已入库（visited）→ 只补关联，不重爬。"""
    aid = _mk_actor('WF-主', 'female')
    t = _mk_task('WF-EXIST')
    r = client.post(f'/api/actors/{aid}/add-work', json={'url': 'https://javdb.com/v/WF-EXIST', 'extract': False})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d['ok'] is True and d['task_id'] == t and d['spawned'] is False and d['created'] is False
    s = SessionLocal()
    assert s.execute(actor_movies.select().where(
        actor_movies.c.actor_id == aid, actor_movies.c.task_id == t)).fetchall()
    s.close()


def test_add_work_new_task_created(client):
    """F2：URL 未入库 → 建 pending 任务 + 列表源（ACTOR_ 命名）+ 关联。"""
    aid = _mk_actor('WF-添加者', 'female')
    url = 'https://javdb.com/v/WFNEW01'
    r = client.post(f'/api/actors/{aid}/add-work', json={'url': url, 'extract': False})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d['ok'] is True and d['created'] is True and d['spawned'] is False
    s = SessionLocal()
    t = s.query(Task).filter(Task.url == url).first()
    assert t is not None and t.status == 'pending'
    ls = s.get(ListSource, t.list_source_id)
    assert ls.list_code.startswith('ACTOR_')
    assert s.execute(actor_movies.select().where(
        actor_movies.c.actor_id == aid, actor_movies.c.task_id == t.id)).fetchall()
    s.close()


def test_add_work_url_validation(client):
    aid = _mk_actor('WF-校验')
    for bad in ('https://example.com/v/ABC', 'ftp://javdb.com/v/ABC', 'not a url'):
        r = client.post(f'/api/actors/{aid}/add-work', json={'url': bad, 'extract': False})
        assert r.status_code == 400, f'{bad} 应 400'


def test_add_work_url_normalized_and_spawn_called(client, monkeypatch):
    """F2：URL 规范化（去 query）+ spawn 以正确参数调用。"""
    import services.single_extract as se
    aid = _mk_actor('WF-抓取', 'female')
    calls = {}

    def fake_spawn(url, actor_id=None, **kw):
        calls['url'] = url
        calls['actor_id'] = actor_id
        return {"ok": True, "message": "已触发提取"}

    monkeypatch.setattr(se, 'spawn_extract_single', fake_spawn)
    url = 'https://javdb.com/v/WFSPAWN?foo=1'
    r = client.post(f'/api/actors/{aid}/add-work', json={'url': url, 'extract': True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d['spawned'] is True
    assert calls['url'] == 'https://javdb.com/v/WFSPAWN'  # query 已去
    assert calls['actor_id'] == aid

def test_add_work_pending_task_respawn(client, monkeypatch):
    """已存在 pending 任务再添加 -> created=False 且触发 spawn（审查 F 缺口）。"""
    import services.single_extract as se
    aid = _mk_actor('WF-再添加', 'female')
    t = _mk_task('WF-PENDING')
    s = SessionLocal()
    task = s.get(Task, t)
    task.status = 'pending'
    s.commit()
    s.close()
    calls = {}

    def fake_spawn(url, actor_id=None, **kw):
        calls['url'] = url
        calls['actor_id'] = actor_id
        return {"ok": True, "message": "已触发提取"}

    monkeypatch.setattr(se, 'spawn_extract_single', fake_spawn)
    r = client.post(f'/api/actors/{aid}/add-work', json={'url': 'https://javdb.com/v/WF-PENDING', 'extract': True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d['created'] is False and d['spawned'] is True
    assert calls['actor_id'] == aid


def test_add_work_spawn_busy(client, monkeypatch):
    """spawn 失败（锁忙）-> 200、spawned=False、message 透传，任务仍入库。"""
    import services.single_extract as se
    aid = _mk_actor('WF-锁忙', 'female')
    monkeypatch.setattr(se, 'spawn_extract_single',
                        lambda url, actor_id=None, **kw: {"ok": False, "message": "已有爬取任务在运行"})
    url = 'https://javdb.com/v/WFBUSY1'
    r = client.post(f'/api/actors/{aid}/add-work', json={'url': url, 'extract': True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d['ok'] is True and d['spawned'] is False
    assert '已有爬取任务' in d['message']
    s = SessionLocal()
    assert s.query(Task).filter(Task.url == url).first() is not None  # 任务仍入库
    s.close()
