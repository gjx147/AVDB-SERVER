"""测试 utils.py 公共工具函数。

注：get_or_404 / paginate 已于 2026-08 打磨批次移除（全项目无引用），
对应测试一并删除；保留 get_setting / set_setting 测试。
"""
from utils import get_setting, set_setting


def test_get_setting_default(db):
    assert get_setting(db, "nonexistent") == ""
    assert get_setting(db, "nonexistent", "fallback") == "fallback"


def test_set_and_get_setting(db):
    set_setting(db, "test_key", "test_value")
    assert get_setting(db, "test_key") == "test_value"
