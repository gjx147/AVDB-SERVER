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

# ══ 演员文件夹（口径：女优/演员名/ 嵌套；默认关手动开；只做下载侧） ══

def test_build_qb_save_path():
    """路径构建：开关/基础路径/演员名三条件缺一不可；嵌套 女优/演员名/。"""
    from routers.downloaders import _build_qb_save_path, _safe_folder_name, _first_actor_name

    # 开关关 -> None（维持原行为）
    assert _build_qb_save_path({"qb_actor_subfolder": "", "qbittorrent_save_path": "/d"}, "三上悠亜") is None
    # 开关开但无基础路径 -> None
    assert _build_qb_save_path({"qb_actor_subfolder": "true", "qbittorrent_save_path": ""}, "三上悠亜") is None
    # 开关开 + 基础路径 + 演员名 -> 女优/演员名
    assert _build_qb_save_path({"qb_actor_subfolder": "true", "qbittorrent_save_path": "/d"}, "三上悠亜") == "/d/女优/三上悠亜"
    # 尾斜杠归一
    assert _build_qb_save_path({"qb_actor_subfolder": "true", "qbittorrent_save_path": "/d/"}, "Lana") == "/d/女优/Lana"
    # 空演员名 -> None（回退原行为）
    assert _build_qb_save_path({"qb_actor_subfolder": "true", "qbittorrent_save_path": "/d"}, "") is None


def test_safe_folder_name():
    from routers.downloaders import _safe_folder_name
    assert _safe_folder_name("三上悠亜") == "三上悠亜"
    assert _safe_folder_name("A/B:C*D?E\F") == "A_B_C_D_E_F"
    assert _safe_folder_name("  .  ") is None  # 消毒后为点
    assert _safe_folder_name("..") is None     # 防目录穿越
    assert _safe_folder_name("") is None
    assert _safe_folder_name("x" * 200) == "x" * 100  # 限长


def test_first_actor_name():
    from routers.downloaders import _first_actor_name
    class T:
        actors = "三上悠亜, 二名, 三名"
    assert _first_actor_name(T()) == "三上悠亜"
    class T2:
        actors = None
    assert _first_actor_name(T2()) is None
    assert _first_actor_name(None) is None


def test_push_magnet_passes_actor_name(client, monkeypatch):
    """手动单推：带任务上下文时解析首位女优传入 _push_qbittorrent。"""
    import routers.downloaders as dl_mod
    from models import ListSource, Task
    from database import SessionLocal
    s = SessionLocal()
    ls = s.query(ListSource).filter(ListSource.list_code == 'AFF').first()
    if not ls:
        ls = ListSource(list_code='AFF', list_path='/aff')
        s.add(ls)
        s.commit()
        s.refresh(ls)
    t = Task(video_code='AFF-001', url='https://javdb.com/v/AFF-001', list_source_id=ls.id,
             status='visited', best_magnet='MAG-AFF-001', actors='三上悠亜, 其他')
    s.add(t)
    s.commit()
    s.refresh(t)
    tid = t.id
    s.close()

    captured = {}

    async def fake_push(magnet, config, actor_name=None):
        captured['actor'] = actor_name
        return {"ok": True, "message": "Ok."}

    dl_mod._push_qbittorrent = fake_push
    r = client.post('/api/downloaders/push', json={'magnet': 'MAG-AFF-001', 'task_id': tid})
    assert r.status_code == 200, r.text
    assert captured.get('actor') == '三上悠亜', f'应传入首位女优，实际 {captured.get("actor")}'

def test_safe_folder_name_windows_reserved():
    """加固 A：Windows 保留名拒绝；截断后不留尾部点。"""
    from routers.downloaders import _safe_folder_name
    for bad in ('CON', 'con', 'NUL', 'COM1', 'lpt3', 'PRN.txt'):
        assert _safe_folder_name(bad) is None, f'{bad} 应拒绝'
    assert _safe_folder_name('x' * 99 + '.y') == 'x' * 99  # 截到 100 后剥掉尾部点（Windows 不允许）


def test_build_qb_save_path_base_variants():
    """加固 A：base 尾分隔符归一（正反斜杠/根路径/病态反斜杠）。"""
    from routers.downloaders import _build_qb_save_path
    cfg = {"qb_actor_subfolder": "true"}
    assert _build_qb_save_path({**cfg, "qbittorrent_save_path": "D:\\Downloads\\"}, "Lana") == "D:\\Downloads/女优/Lana"
    assert _build_qb_save_path({**cfg, "qbittorrent_save_path": "/"}, "Lana") == "/女优/Lana"
    assert _build_qb_save_path({**cfg, "qbittorrent_save_path": "\\"}, "Lana") is None


def test_build_qb_save_path_switch_semantics():
    """加固 C：仅 'true'/'1'（大小写/空白容忍）视为开；'0'/'false'/空 视为关。"""
    from routers.downloaders import _build_qb_save_path
    base = {"qbittorrent_save_path": "/d"}
    assert _build_qb_save_path({**base, "qb_actor_subfolder": "true"}, "A") is not None
    assert _build_qb_save_path({**base, "qb_actor_subfolder": " TRUE "}, "A") is not None
    assert _build_qb_save_path({**base, "qb_actor_subfolder": "1"}, "A") is not None
    for off in ("0", "false", "", "  ", "yes"):
        assert _build_qb_save_path({**base, "qb_actor_subfolder": off}, "A") is None, off


def test_push_sync_fallback_and_nested(client, monkeypatch):
    """加固 F：同步推送回退分支——开关关=原路径；开关开=嵌套路径。"""
    import routers.downloaders as dl_mod
    captured = {}

    class FakeQB:
        def auth_log_in(self):
            pass

        def torrents_add(self, urls=None, save_path=None):
            captured['save_path'] = save_path
            return "Ok."

        def auth_log_out(self):
            pass

    class FakeMod:
        Client = lambda *a, **kw: FakeQB()  # noqa: E731

    import qbittorrentapi
    monkeypatch.setattr(qbittorrentapi, 'Client', FakeMod.Client)

    # 开关关：用原路径
    cfg_off = {"qb_url": "http://x", "qbittorrent_save_path": "/d", "qb_actor_subfolder": ""}
    r = dl_mod._push_qbittorrent_sync('MAG-1', cfg_off, actor_name='三上悠亜')
    assert r['ok'] and captured['save_path'] == '/d'
    # 开关开：嵌套
    cfg_on = {"qb_url": "http://x", "qbittorrent_save_path": "/d", "qb_actor_subfolder": "true"}
    r = dl_mod._push_qbittorrent_sync('MAG-1', cfg_on, actor_name='三上悠亜')
    assert r['ok'] and captured['save_path'] == '/d/女优/三上悠亜'


def test_batch_push_passes_actor_name(client, monkeypatch):
    """加固 F：批量入口也传递首位女优。"""
    import routers.downloaders as dl_mod
    from models import ListSource, Task
    from database import SessionLocal
    s = SessionLocal()
    ls = s.query(ListSource).filter(ListSource.list_code == 'AFF').first()
    t = Task(video_code='AFF-002', url='https://javdb.com/v/AFF-002', list_source_id=ls.id,
             status='visited', best_magnet='MAG-AFF-002', actors='Lana Rhoades, 其他')
    s.add(t)
    s.commit()
    s.refresh(t)
    tid = t.id
    s.close()

    captured = {}

    async def fake_push(magnet, config, actor_name=None):
        captured['actor'] = actor_name
        return {"ok": True, "message": "Ok."}

    dl_mod._push_qbittorrent = fake_push
    r = client.post('/api/tasks/batch-push', json={'task_ids': [tid], 'downloader': 'qbittorrent'})
    assert r.status_code == 200, r.text
    assert captured.get('actor') == 'Lana Rhoades'

def test_qb_health_endpoint(client, monkeypatch):
    """qb-health：返回版本/连接状态/DHT 节点数。"""
    import routers.downloaders as dl_mod

    class FakeQB:
        def auth_log_in(self):
            pass

        def app_version(self):
            return 'v5.0.3'

        def sync_maindata(self):
            return {"server_state": {"connection_status": "firewalled", "dht_nodes": 12}}

        def auth_log_out(self):
            pass

    class FakeMod:
        Client = lambda *a, **kw: FakeQB()  # noqa: E731

    import qbittorrentapi
    monkeypatch.setattr(qbittorrentapi, 'Client', FakeMod.Client)
    r = client.post('/api/downloaders/qb-health')
    assert r.status_code == 200, r.text
    d = r.json()
    assert d['ok'] is True and d['version'] == 'v5.0.3'
    assert d['connection_status'] == 'firewalled' and d['dht_nodes'] == 12
