"""智能下载策略（N23）：按演员/厂牌路由到首选下载器。"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger("avdb.download_strategy")


def get_strategy(db) -> dict:
    """读取策略（settings key download_strategy，JSON）。"""
    from models import Setting
    row = db.get(Setting, "download_strategy")
    try:
        return json.loads(row.value) if row and row.value else {}
    except Exception:
        return {}


def pick_downloader(db, task) -> str:
    """按策略选择下载器（策略优先，其次默认下载器）。"""
    from routers.downloaders import _get_setting
    strategy = get_strategy(db)
    default = _get_setting(db, "default_downloader") or "qbittorrent"
    # 演员优先
    t_actors = [a.strip() for a in (task.actors or "").split(",") if a.strip()]
    for a in t_actors:
        d = strategy.get("actors", {}).get(a)
        if d:
            return d
    # 厂牌
    if task.maker:
        d = strategy.get("makers", {}).get(task.maker.strip())
        if d:
            return d
    return strategy.get("default", default)


async def push_with_strategy(task_id: int) -> dict:
    """按策略推送单个任务（规则引擎动作与批量推送共用）。"""
    from database import SessionLocal
    from models import Download, Task
    from routers.downloaders import _extract_hash, _get_setting, _push_clouddrive, _push_qbittorrent

    db = SessionLocal()
    try:
        t = db.get(Task, task_id)
        if not t or not t.best_magnet:
            return {"ok": False, "message": "任务无磁力"}
        downloader = pick_downloader(db, t)
        config_keys = (
            "qb_url", "qb_username", "qb_password", "qbittorrent_save_path",
            "clouddrive_url", "clouddrive_token", "clouddrive_username",
            "clouddrive_password", "clouddrive_save_path",
        )
        config = {k: _get_setting(db, k) for k in config_keys}
        try:
            if downloader == "clouddrive":
                result = await _push_clouddrive(t.best_magnet, config)
            else:
                downloader = "qbittorrent"
                result = await _push_qbittorrent(t.best_magnet, config)
        except Exception as e:
            return {"ok": False, "message": str(e)[:120]}
        if result.get("ok"):
            db.add(Download(
                task_id=t.id, video_code=t.video_code, magnet=t.best_magnet,
                info_hash=_extract_hash(t.best_magnet), downloader=downloader,
                status="pushed",
            ))
            db.commit()
            return {"ok": True, "downloader": downloader}
        return {"ok": False, "message": result.get("error") or "推送失败"}
    finally:
        db.close()
