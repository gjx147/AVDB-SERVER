# -*- coding: utf-8 -*-
"""迅雷通道回归：token / 客户端打桩 / 轮询映射 / 磁力导出 / 推送分支 / helper。"""
import os
import sys
import asyncio
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from conftest import client, db  # noqa: F401,E402

from models import Download, ListSource, Setting, Task
from database import SessionLocal


def _mk_task(code, magnet=None, magnets_json=None):
    magnet = 'MAG-' + code if magnet is None else magnet
    s = SessionLocal()
    ls = s.query(ListSource).filter(ListSource.list_code == 'XL').first()
    if not ls:
        ls = ListSource(list_code='XL', list_path='/xl')
        s.add(ls)
        s.commit()
        s.refresh(ls)
    t = Task(video_code=code, url=f'https://javdb.com/v/{code}', list_source_id=ls.id,
             status='visited', best_magnet=magnet, magnets_json=magnets_json)
    s.add(t)
    s.commit()
    s.refresh(t)
    tid = t.id
    s.close()
    return tid


# ══ 1. 通用 helper（qB 时代保留） ══

def test_safe_folder_name():
    from routers.downloaders import _safe_folder_name
    assert _safe_folder_name("三上悠亜") == "三上悠亜"
    assert _safe_folder_name("A/B:C*D?E\\F") == "A_B_C_D_E_F"
    assert _safe_folder_name("  .  ") is None
    assert _safe_folder_name("..") is None
    assert _safe_folder_name("CON") is None
    assert _safe_folder_name("x" * 200) == "x" * 100


def test_first_actor_name():
    from routers.downloaders import _first_actor_name

    class T:
        actors = "三上悠亜, 二名"
    assert _first_actor_name(T()) == "三上悠亜"


def test_get_setting_alias(db):
    from services.settings_util import get_setting
    s = SessionLocal()
    s.add(Setting(key='qbittorrent_url', value='http://127.0.0.1:8080'))
    s.commit()
    s.close()
    assert get_setting(db, 'qb_url') == 'http://127.0.0.1:8080'
    assert get_setting(db, 'not_exist_key', default='x') == 'x'


# ══ 2. XunleiClient ══

def test_xunlei_token_low_version():
    """低版本 token：<unix秒>.<md5(秒+SECRET)>。"""
    from services.xunlei_client import _md5_hex, SECRET
    e = int(time.time())
    tok = f"{e}.{_md5_hex(str(e) + SECRET)}"
    assert tok.startswith(str(e) + ".") and len(tok.split(".")[1]) == 32


def test_xunlei_client_add_task_mock():
    """MockTransport 打桩：版本探测→deviceId→目录→提交，断言请求体与成功判定。"""
    import json as _json
    import httpx
    from services.xunlei_client import XunleiClient
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        u = request.url.path
        if u.endswith('/launcher/status'):
            return httpx.Response(200, json={"running_version": "3.15.0"})
        if '/drive/v1/tasks' in u and 'type=user%23runner' in str(request.url):
            return httpx.Response(200, json={"tasks": [{"params": {"target": "DEV123"}}]})
        if '/drive/v1/files' in u:
            return httpx.Response(200, json={"files": [{"parent_id": "ROOT1"}]})
        if u.endswith('/drive/v1/task'):
            seen['body'] = _json.loads(request.content)
            return httpx.Response(200, json={"HttpStatus": 0})
        return httpx.Response(404, json={})

    tr = httpx.MockTransport(handler)
    c = XunleiClient("http://127.0.0.1:2345", transport=tr)
    r = c.add_task("magnet:?xt=urn:btih:" + "a" * 40, "ABC-123")
    assert r["ok"] is True
    b = seen["body"]
    assert b["type"] == "user#download-url"
    assert b["name"] == "ABC-123"
    assert b["space"] == "DEV123"
    assert b["params"]["url"].startswith("magnet:")
    assert b["params"]["parent_folder_id"] == "ROOT1"


def test_xunlei_client_uiauth_high_version():
    """高版本（≥3.21.0）：从首页 HTML 的 uiauth 提取 token。"""
    import httpx
    from services.xunlei_client import XunleiClient

    def handler(request: httpx.Request) -> httpx.Response:
        u = request.url.path
        if u.endswith('/launcher/status'):
            return httpx.Response(200, json={"running_version": "3.25.0"})
        if u.startswith('/webman/3rdparty/pan-xunlei-com/index.cgi'):
            return httpx.Response(200, text='function uiauth(value){ return "MYTOKEN123" }')
        return httpx.Response(404, json={})

    tr = httpx.MockTransport(handler)
    c = XunleiClient("http://127.0.0.1:2345", transport=tr)
    assert c._pan_auth(c._client()) == "MYTOKEN123"


# ══ 3. 轮询映射 ══

def test_tracker_poll_xunlei_maps_status():
    """fake 迅雷任务列表 → Download/Task 状态同步（含 completed 写 completed_at）。"""
    import services.download_tracker as tracker_mod
    s = SessionLocal()
    tid = _mk_task('XL-SYNC', magnet='magnet:?xt=urn:btih:' + 'b' * 40)
    dl = Download(task_id=tid, video_code='XL-SYNC', magnet='MAG', info_hash='b' * 40,
                  downloader='xunlei', status='pushed')
    s.add(dl)
    s.commit()
    s.refresh(dl)
    s.close()

    def fake_sync(config):
        return [{"name": "XL-SYNC", "phase": "PHASE_TYPE_RUNNING", "progress": 45,
                 "speed": 1024, "real_path": "/xl/t.mkv"}]

    tracker_mod._poll_xunlei_sync = fake_sync
    tracker_mod._get_setting = lambda d, k, *a, **kw: 'http://x'  # noqa: E731

    s2 = SessionLocal()
    try:
        asyncio.run(tracker_mod._poll_xunlei(s2))
    finally:
        s2.close()

    s3 = SessionLocal()
    try:
        t = s3.get(Task, tid)
        assert t.download_status == 'downloading'
        dl2 = s3.query(Download).filter(Download.task_id == tid).first()
        assert dl2.status == 'downloading' and dl2.progress == 45
    finally:
        s3.close()


def test_tracker_poll_xunlei_complete():
    import services.download_tracker as tracker_mod
    s = SessionLocal()
    tid = _mk_task('XL-DONE', magnet='magnet:?xt=urn:btih:' + 'c' * 40)
    dl = Download(task_id=tid, video_code='XL-DONE', magnet='MAG', info_hash='c' * 40,
                  downloader='xunlei', status='downloading')
    s.add(dl)
    s.commit()
    s.close()

    tracker_mod._poll_xunlei_sync = lambda config: [
        {"name": "XL-DONE", "phase": "PHASE_TYPE_COMPLETE", "progress": 100, "speed": 0, "real_path": "/xl/d.mkv"}]
    tracker_mod._get_setting = lambda d, k, *a, **kw: 'http://x'  # noqa: E731

    s2 = SessionLocal()
    try:
        asyncio.run(tracker_mod._poll_xunlei(s2))
    finally:
        s2.close()

    s3 = SessionLocal()
    try:
        t = s3.get(Task, tid)
        assert t.download_status == 'completed'
        dl2 = s3.query(Download).filter(Download.task_id == tid).first()
        assert dl2.status == 'completed' and dl2.completed_at is not None
    finally:
        s3.close()


# ══ 4. 批量磁力导出 ══

def test_batch_magnets_endpoint(client):
    t1 = _mk_task('XL-M1', magnets_json='[]')
    t2 = _mk_task('XL-M2', magnet='MAG-XL-M2')
    r = client.post('/api/tasks/batch-magnets', json={'task_ids': [t1, t2]})
    assert r.status_code == 200, r.text
    items = {i['task_id']: i for i in r.json()['items']}
    assert items[t1]['has_magnet'] is True and items[t1]['magnet'] == 'MAG-XL-M1'  # best_magnet 兜底
    assert items[t2]['magnet'] == 'MAG-XL-M2'


def test_batch_magnets_json_fallback():
    """magnets_json 形态归一：dict{magnet}/dict{link}/string。"""
    import json
    from routers.tasks import _first_magnet
    assert _first_magnet(json.dumps([{"magnet": "magnet:?a"}])) == "magnet:?a"
    assert _first_magnet(json.dumps([{"link": "magnet:?b"}])) == "magnet:?b"
    assert _first_magnet(json.dumps(["magnet:?c"])) == "magnet:?c"
    assert _first_magnet(json.dumps([{"url": "magnet:?d"}])) is None
    assert _first_magnet(None) is None


# ══ 5. 推送分支 ══

def test_push_magnet_xunlei_branch(client, monkeypatch):
    import routers.downloaders as dl_mod
    tid = _mk_task('XL-PUSH', magnet='MAG-XL-PUSH')
    captured = {}

    async def fake_push(magnet, config):
        captured['magnet'] = magnet
        captured['name'] = config.get('_task_name')
        return {"ok": True, "message": "已提交迅雷下载"}

    dl_mod._push_xunlei = fake_push
    r = client.post('/api/downloaders/push', json={'magnet': 'MAG-XL-PUSH', 'task_id': tid})
    assert r.status_code == 200, r.text
    assert captured['magnet'] == 'MAG-XL-PUSH'
    assert captured['name'] == 'XL-PUSH'  # 番号作为任务名（轮询匹配键）


def test_batch_push_whitelist_xunlei(client, monkeypatch):
    import routers.downloaders as dl_mod
    tid = _mk_task('XL-BATCH', magnet='MAG-XL-BATCH')
    captured = {}

    async def fake_push(magnet, config):
        captured['name'] = config.get('_task_name')
        return {"ok": True, "message": "ok"}

    dl_mod._push_xunlei = fake_push
    r = client.post('/api/tasks/batch-push', json={'task_ids': [tid], 'downloader': 'xunlei'})
    assert r.status_code == 200, r.text
    assert captured['name'] == 'XL-BATCH'
    # qbittorrent 已移出白名单
    r2 = client.post('/api/tasks/batch-push', json={'task_ids': [tid], 'downloader': 'qbittorrent'})
    assert r2.status_code == 400, r2.text

def test_push_legacy_default_downloader_normalized(client, monkeypatch):
    """S3 回归：旧默认值 qbittorrent 归一为 xunlei，落库 downloader=xunlei。"""
    import routers.downloaders as dl_mod
    tid = _mk_task('XL-LEGACY', magnet='MAG-XL-LEGACY')
    s0 = SessionLocal()
    s0.add(Setting(key='default_downloader', value='qbittorrent'))
    s0.commit()
    s0.close()
    captured = {}

    async def fake_push(magnet, config):
        captured['name'] = config.get('_task_name')
        return {"ok": True, "message": "ok"}

    dl_mod._push_xunlei = fake_push
    r = client.post('/api/downloaders/push', json={'magnet': 'MAG-XL-LEGACY', 'task_id': tid})
    assert r.status_code == 200, r.text
    assert captured['name'] == 'XL-LEGACY'
    s1 = SessionLocal()
    dl = s1.query(Download).filter(Download.task_id == tid).first()
    assert dl is not None and dl.downloader == 'xunlei', f'应落库 xunlei，实际 {dl.downloader if dl else None}'
    s1.close()


def test_push_xunlei_requires_video_code(client):
    """S4 回归：无番号（无 task_id/无 video_code）推送直接失败并给指引。"""
    r = client.post('/api/downloaders/push', json={'magnet': 'MAG-NOCODE'})
    assert r.status_code == 200, r.text
    assert r.json().get('ok') is False and '番号' in (r.json().get('message') or '')


def test_poll_missing_three_rounds_fail():
    """A3 回归：连续 3 轮未匹配到迅雷任务 → failed。"""
    import services.download_tracker as tracker_mod
    s = SessionLocal()
    tid = _mk_task('XL-GONE', magnet='magnet:?xt=urn:btih:' + 'd' * 40)
    dl = Download(task_id=tid, video_code='XL-GONE', magnet='MAG', info_hash='d' * 40,
                  downloader='xunlei', status='pushed')
    s.add(dl)
    s.commit()
    dl_id = dl.id
    s.close()

    tracker_mod._poll_xunlei_sync = lambda config: []  # 容器在线但无该任务
    tracker_mod._get_setting = lambda d, k, *a, **kw: 'http://x'  # noqa: E731
    for _ in range(3):
        s2 = SessionLocal()
        try:
            asyncio.run(tracker_mod._poll_xunlei(s2))
        finally:
            s2.close()
    s3 = SessionLocal()
    try:
        dl2 = s3.get(Download, dl_id)
        assert dl2.status == 'failed', f'3 轮未命中应 failed，实际 {dl2.status}'
    finally:
        s3.close()


def test_organize_run_all_no_name_error():
    """S1 回归：run_organize_all 不再引用已删函数（仅 CD2 路径）。"""
    import asyncio
    from services import organizer
    r = asyncio.run(organizer.run_organize_all())
    assert isinstance(r, dict) and 'ok' in r
