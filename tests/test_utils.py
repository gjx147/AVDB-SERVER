"""测试 utils.py 公共工具函数。"""
from utils import get_setting, set_setting, get_or_404


def test_get_setting_default(db):
    assert get_setting(db, "nonexistent") == ""
    assert get_setting(db, "nonexistent", "fallback") == "fallback"


def test_set_and_get_setting(db):
    set_setting(db, "test_key", "test_value")
    assert get_setting(db, "test_key") == "test_value"


def test_get_or_404_found(db):
    from models import ListSource
    src = ListSource(list_code="TEST", list_path="/test")
    db.add(src); db.commit()
    obj = get_or_404(db, ListSource, src.id, "列表源")
    assert obj.id == src.id


def test_get_or_404_not_found(db):
    from fastapi import HTTPException
    from models import ListSource
    try:
        get_or_404(db, ListSource, 99999, "列表源")
        assert False, "应该抛出 404"
    except HTTPException as e:
        assert e.status_code == 404
        assert "列表源" in e.detail
