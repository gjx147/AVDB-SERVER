"""settings 键读取统一入口（含 qbittorrent_* 等键名别名兼容）。

盘点修复①：此前只有 routers/downloaders.py 的 _get_setting 带别名映射，
tracker / organizer 各自本地实现直读短键——前端只存 qbittorrent_url 长键时
轮询/整理链路静默失效。这里统一收口，各模块委托本函数。
"""
from models import Setting

# 后端短键 → 前端长键别名（原 downloaders.py 映射）
_ALIASES = {
    "qb_url": "qbittorrent_url",
    "qb_username": "qbittorrent_username",
    "qb_password": "qbittorrent_password",
    "aria2_url": "aria2_rpc_url",
    "aria2_secret": "aria2_token",
}


def get_setting(db, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    if row and row.value:
        return row.value
    alias = _ALIASES.get(key)
    if alias:
        row = db.get(Setting, alias)
        if row and row.value:
            return row.value
    return default
