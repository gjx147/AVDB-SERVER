# -*- coding: utf-8 -*-
"""qb 盘点修复回归：①别名统一入口 ②Task.download_status 接通 ③Agent 工具请求构造。"""
import os
import sys
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from conftest import client, db  # noqa: F401,E402

from models import Download, ListSource, Setting, Task
from database import SessionLocal


def _mk_task(code, magnet=None):
    magnet = 'MAG-' + code if magnet is None else magnet
    s = SessionLocal()
    ls = s.query(ListSource).filter(ListSource.list_code == 'QBF').first()
    if not ls:
        ls = ListSource(list_code='QBF', list_path='/qbf')
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


def test_get_setting_alias(db):
    """固定①：长键 qbittorrent_url 短键读取可用（统一入口）。"""
    from services.settings_util import get_setting
    s = SessionLocal()
    s.add(Setting(key='qbittorrent_url', value='http://127.0.0.1:8080'))
    s.commit()
    s.close()
    assert get_setting(db, 'qb_url') == 'http://127.0.0.1:8080'
    assert get_setting(db, 'qb_url', default='x') == 'http://127.0.0.1:8080'
    assert get_setting(db, 'not_exist_key', default='x') == 'x'


def test_tracker_syncs_task_download_status():
    """固定②：轮询回写后 Task.download_status 同步（此前死字段无写入）。"""
    import services.download_tracker as tracker_mod
    s = SessionLocal()
    tid = _mk_task('QBF-SYNC', magnet='MAG-QBF-SYNC')
    dl = Download(task_id=tid, video_code='QBF-SYNC', magnet='MAG-QBF-SYNC',
                  info_hash='a' * 40, downloader='qbittorrent', status='pushed')
    s.add(dl)
    s.commit()
    s.refresh(dl)
    dl_id = dl.info_hash  # 保存后 info_hash
    hash_val = dl.info_hash
    s.close()

    def fake_sync(config, hashes):  # 同步：_poll_qbittorrent 走 asyncio.to_thread
        return [{"id": did, "status": "completed", "progress": 100.0, "error": None}
                for did, _ in hashes]

    tracker_mod._poll_qbittorrent_sync = fake_sync
    tracker_mod._get_setting = lambda d, k, *a, **kw: 'http://x'  # noqa: E731
    import services.organizer as org

    async def _noop_organize(*a, **kw):
        return None

    org.trigger_organize = _noop_organize  # 挡掉真实整理触发（create_task 需要协程）

    s2 = SessionLocal()
    try:
        asyncio.run(tracker_mod._poll_qbittorrent(s2))
    finally:
        s2.close()

    s3 = SessionLocal()
    try:
        t = s3.get(Task, tid)
        assert t.download_status == 'completed', f'Task.download_status 应同步为 completed，实际 {t.download_status}'
        dl2 = s3.query(Download).filter(Download.task_id == tid).first()
        assert dl2.status == 'completed'
    finally:
        s3.close()


def test_agent_push_download_builds_request():
    """固定③a：Agent 单推工具正确构造 PushRequest 端点签名（此前传 dict 必 TypeError）。"""
    import services.agent_service as agent_mod
    from routers.downloaders import PushRequest
    s = SessionLocal()
    tid = _mk_task('QBF-AGENT', magnet='MAG-QBF-AGENT')
    s.close()

    captured = {}

    async def fake_push(req, dbu, user):
        captured['req'] = req
        captured['user'] = user
        return {"ok": True, "download_id": 1, "message": "已触发提取"}

    import routers.downloaders as dl_mod
    dl_mod.push_magnet = fake_push
    agent_mod._bg_submit = lambda key, fn: fn()  # 同步执行后台函数
    agent_mod._bg_job_record = lambda *a, **kw: None  # noqa: E731

    s2 = SessionLocal()
    try:
        r = agent_mod._push_download(s2, {'task_id': tid})
    finally:
        s2.close()
    assert r.get('ok') is True
    req = captured.get('req')
    assert isinstance(req, PushRequest), f'应为 PushRequest，实际 {type(req)}'
    assert req.task_id == tid and req.magnet == 'MAG-QBF-AGENT'


def test_agent_batch_push_builds_request():
    """固定③b：Agent 批量工具正确构造 BatchViewRequest。"""
    import services.agent_service as agent_mod
    from routers.tasks import BatchViewRequest
    s = SessionLocal()
    t1 = _mk_task('QBF-B1')
    t2 = _mk_task('QBF-B2')
    s.close()

    captured = {}

    async def fake_batch(payload, dbu, user):
        captured['payload'] = payload
        return {"pushed": 2, "skipped": 0}

    import routers.tasks as tasks_mod
    tasks_mod.batch_push = fake_batch
    agent_mod._bg_job_record = lambda *a, **kw: None  # noqa: E731

    s2 = SessionLocal()
    try:
        r = agent_mod._batch_push(s2, {'task_ids': [t1, t2]})
    finally:
        s2.close()
    assert r.get('ok') is True
    payload = captured.get('payload')
    assert isinstance(payload, BatchViewRequest), f'应为 BatchViewRequest，实际 {type(payload)}'
    assert sorted(payload.task_ids) == sorted([t1, t2])