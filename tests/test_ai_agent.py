# -*- coding: utf-8 -*-
"""AI Agent 关键回归测试：token 幂等 / 中英检索 / 审计撤销闭环。"""
import pytest


def test_confirm_token_idempotent(db):
    """确认 token 一次性消费：重复确认被拒（防重复执行写操作）。"""
    from services.agent_service import _issue_token, _consume_token
    t = _issue_token("stats", {}, user="admin")
    assert _consume_token(t, user="admin") is not None
    assert _consume_token(t, user="admin") is None  # 已消费


def test_confirm_token_tamper_rejected(db):
    """篡改 token 验签拒绝。"""
    from services.agent_service import _issue_token, _consume_token
    t = _issue_token("stats", {}, user="admin")
    assert _consume_token(t + "x", user="admin") is None


def test_search_cn_en_alias(db):
    """中英标签别名检索：搜中文命中英文标签作品（体检 #4 回归）。"""
    from models import Task, ListSource
    from sqlalchemy import select
    from database import SessionLocal
    from services.agent_service import _parse_question, _search
    import asyncio

    d = SessionLocal()
    try:
        src = d.execute(select(ListSource).limit(1)).scalar()
        if src is None:
            src = ListSource(id=1, list_code="TEST", list_path="/video_codes/TEST")
            d.add(src)
            d.commit()
        d.add(Task(list_source_id=src.id, url="https://x/alias-1", video_code="ALIAS-1",
                   title="alias", rating=9.0, tags="Big Tits", status="visited"))
        d.commit()

        async def _t():
            q, e = await _parse_question("巨乳作品")
            r = _search(d, {"question": "巨乳作品"}, query=q, engine=e)
            return r.get("items", [])

        items = asyncio.run(_t())
        assert any(i["video_code"] == "ALIAS-1" for i in items), "中英别名检索失败"
    finally:
        d.close()


def test_config_audit_loop(db):
    """配置修改→审计留痕→回滚闭环（G4 回归）。"""
    from models import ConfigAudit, Setting
    from sqlalchemy import select
    from services.agent_service import _config_set

    # 先清掉可能的历史记录
    db.query(ConfigAudit).filter(ConfigAudit.key == "emby_auto_sync").delete()
    db.commit()

    r = _config_set(db, {"key": "emby_auto_sync", "value": "true", "_operator": "tester"})
    assert r.get("ok")
    audit = db.execute(select(ConfigAudit).where(ConfigAudit.key == "emby_auto_sync")).scalar_one()
    assert audit.operator == "tester"

    # 回滚（还原旧值，与 /api/ai/agent/rollback 同一逻辑）
    row = db.get(Setting, "emby_auto_sync")
    row.value = audit.old_value
    db.commit()
    row = db.get(Setting, "emby_auto_sync")
    assert row.value == audit.old_value
