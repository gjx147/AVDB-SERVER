"""CD2 自动迁移 + CMS 入库通知。

推送磁力成功后延迟触发：
1. CD2 GetSubFiles 列源文件夹，找匹配 video_code 的视频文件
2. CD2 CreateFolder 在媒体库建女优子目录（幂等）
3. CD2 MoveFile 把文件从源文件夹移到 /媒体库/女优名/
4. 调 CMS /api/sync/lift_by_token?type=auto_organize 通知入库（若 cms_enabled）

不生成 strm —— strm 由 CMS 自己处理。

settings 表 key：
- cd2_organize_enabled: 总开关
- cd2_organize_source_folder: CD2 离线下载文件夹（迁移源）
- cd2_organize_target_folder: 媒体库根目录（迁移目标）
- cd2_organize_delay_seconds: 延迟触发秒数（默认 120，给 CD2 下载时间）
复用 clouddrive_url/token/username/password（CD2 登录）
复用 cms_enabled/url/token（CMS 通知，可选）
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from urllib.parse import unquote

logger = logging.getLogger("avdb.downloaders.cd2")

# 视频扩展名（用于过滤 CD2 下载目录里的小文件如 .jpg/.txt）
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".wmv", ".mov", ".m4v", ".ts", ".rmvb", ".iso"}


def _get_config() -> dict[str, str]:
    """从 settings 表读所有相关配置。"""
    from database import SessionLocal
    from models import Setting
    keys = [
        "cd2_organize_enabled", "cd2_organize_source_folder",
        "cd2_organize_target_folder", "cd2_organize_delay_seconds",
        "clouddrive_url", "clouddrive_token", "clouddrive_username", "clouddrive_password",
        "cms_enabled", "cms_url", "cms_token",
    ]
    db = SessionLocal()
    try:
        result = {}
        for k in keys:
            row = db.get(Setting, k)
            if row and row.value is not None:
                result[k] = row.value
        return result
    finally:
        db.close()


def _to_bool(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def _sanitize_folder_name(name: str) -> str:
    """清理 Windows/Linux 文件系统非法字符。"""
    if not name:
        return "未分类"
    # 替换非法字符为下划线
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    # 去除首尾空格和点（Windows 不允许）
    cleaned = cleaned.strip(". ")
    return cleaned or "未分类"


def _get_lead_actress_name(db, task) -> str:
    """从 task.actors 文本字段取第一个名字，查 actors 表过滤 gender=female。

    找不到女优记录时回退 task.actors 第一个名字；无演员字段回退 '未分类'。
    """
    actors_str = (task.actors or "").strip()
    if not actors_str:
        return "未分类"
    names = [n.strip() for n in actors_str.split(",") if n.strip()]
    if not names:
        return "未分类"
    first_name = names[0]

    # 查 actors 表，优先返回 gender=female 的第一个匹配
    try:
        from models import Actor
        from sqlalchemy import select
        rows = db.execute(
            select(Actor).where(Actor.name.in_(names)).order_by(Actor.id)
        ).scalars().all()
        # 先找女优
        for r in rows:
            if (r.gender or "").lower() == "female":
                return _sanitize_folder_name(r.name)
        # 没女优就用第一个匹配的演员
        if rows:
            return _sanitize_folder_name(rows[0].name)
    except Exception as e:
        logger.warning(f"[CD2迁移] 查询演员表失败，回退用 task.actors 文本: {e}")

    return _sanitize_folder_name(first_name)


def _get_downloaded_filename(task) -> str:
    """推断 CD2 下载后的文件名。

    优先级：task.magnets_json 里匹配 task.best_magnet 的 name → magnet &dn= 参数 → {video_code}.mp4
    """
    # 1. 从 magnets_json 找 best_magnet 对应的 name
    best_magnet = (task.best_magnet or "").strip()
    magnets_json = task.magnets_json or ""
    if best_magnet and magnets_json:
        try:
            magnets = json.loads(magnets_json)
            if isinstance(magnets, list):
                for m in magnets:
                    if isinstance(m, dict) and m.get("magnet") == best_magnet:
                        name = (m.get("name") or "").strip()
                        if name:
                            return name
        except Exception:
            pass

    # 2. 解析 magnet 的 &dn= 参数（display name）
    if best_magnet:
        m = re.search(r"[&?]dn=([^&]+)", best_magnet)
        if m:
            dn = unquote(m.group(1)).strip()
            if dn:
                return dn

    # 3. 回退番号
    return f"{task.video_code or 'unknown'}.mp4"


def _match_video_files(files: list[dict], video_code: str | None) -> list[dict]:
    """从 list_folder 结果里匹配属于该 video_code 的视频文件。

    匹配规则：文件名（不区分大小写）包含 video_code，且扩展名是视频。
    无 video_code 时返回所有视频文件（保守：只匹配明显的视频文件名）。
    """
    result = []
    for f in files:
        if f.get("is_directory"):
            continue
        name = f.get("name", "")
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in VIDEO_EXTS:
            continue
        if video_code:
            if video_code.lower() in name.lower():
                result.append(f)
        else:
            # 无番号时，避免误移所有视频（保守返回空）
            pass
    return result


async def _do_organize(task_id: int, video_code: str | None, delay: int):
    """延迟任务主体。"""
    label = f"task_id={task_id} video_code={video_code or '无'}"
    logger.info(f"[CD2迁移] 计划在 {delay}s 后检查源文件夹 ({label})")
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        logger.info(f"[CD2迁移] 延迟任务已取消 ({label})")
        raise

    # 重读配置（延迟期间用户可能改了设置）
    cfg = _get_config()
    source_folder = cfg.get("cd2_organize_source_folder", "").strip()
    target_folder = cfg.get("cd2_organize_target_folder", "").strip()
    cd2_url = cfg.get("clouddrive_url", "").strip()
    if not cd2_url:
        logger.warning(f"[CD2迁移] 跳过：clouddrive_url 未配置 ({label})")
        return
    if not source_folder or not target_folder:
        logger.warning(f"[CD2迁移] 跳过：source/target 文件夹未配置 ({label})")
        return

    # 查 Task 拿女优名
    from database import SessionLocal
    from models import Task
    db = SessionLocal()
    try:
        task = db.get(Task, task_id) if task_id else None
        actress_name = _get_lead_actress_name(db, task) if task else "未分类"
        expected_filename = _get_downloaded_filename(task) if task else ""
        logger.info(f"[CD2迁移] 开始 ({label}): 女优={actress_name} 预期文件={expected_filename}")
    finally:
        db.close()

    # CD2 登录
    from services.cd2_client import get_token_or_login, list_folder, create_folder, move_file
    token, err = await get_token_or_login(cfg)
    if err:
        logger.error(f"[CD2迁移] CD2 登录失败 ({label}): {err}")
        return

    # 列源文件夹，找匹配 video_code 的视频文件
    files, list_err = await list_folder(cd2_url, token, source_folder)
    if list_err:
        logger.error(f"[CD2迁移] 列源文件夹失败 ({label}): {list_err}")
        return
    logger.info(f"[CD2迁移] 源文件夹 {source_folder} 共 {len(files)} 个条目")

    matched = _match_video_files(files, video_code)
    if not matched:
        logger.info(f"[CD2迁移] 源文件夹未找到匹配 video_code={video_code} 的视频文件，可能 CD2 还在下载 ({label})")
        return

    logger.info(f"[CD2迁移] 找到 {len(matched)} 个匹配文件: {[f['name'] for f in matched]}")

    # 在媒体库建女优子目录（幂等）
    dest_path = f"{target_folder.rstrip('/')}/{actress_name}"
    ok, msg = await create_folder(cd2_url, token, target_folder, actress_name)
    if not ok:
        logger.error(f"[CD2迁移] 创建目录 {dest_path} 失败 ({label}): {msg}")
        return
    logger.info(f"[CD2迁移] 目录就绪: {dest_path}")

    # 移动文件
    file_paths = [f["full_path"] for f in matched if f.get("full_path")]
    if not file_paths:
        logger.error(f"[CD2迁移] 匹配文件缺少 full_path 字段 ({label})")
        return
    ok, msg = await move_file(cd2_url, token, file_paths, dest_path)
    if ok:
        logger.info(f"[CD2迁移] 移动成功 ({label}): {len(file_paths)} 个文件 → {dest_path}")
    else:
        logger.error(f"[CD2迁移] 移动失败 ({label}): {msg}")
        return

    # 可选：更新 Task.media_in_library
    try:
        db = SessionLocal()
        try:
            task = db.get(Task, task_id) if task_id else None
            if task:
                task.media_in_library = True
                db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[CD2迁移] 更新 media_in_library 失败（不影响主流程）: {e}")

    # 可选：通知 CMS 入库
    if _to_bool(cfg.get("cms_enabled")):
        cms_url = cfg.get("cms_url", "").strip()
        cms_token = cfg.get("cms_token", "").strip() or "cloud_media_sync"
        if cms_url:
            try:
                from services.cms_sync import _trigger_sync
                result = await _trigger_sync(cms_url, cms_token)
                if result["status_code"] == 200:
                    logger.info(f"[CD2迁移] CMS 入库通知成功 ({label})")
                else:
                    logger.warning(f"[CD2迁移] CMS 入库通知返回 HTTP {result['status_code']} ({label})")
            except Exception as e:
                logger.warning(f"[CD2迁移] CMS 入库通知异常（不影响主流程）: {e}")


def schedule_organize(task_id: int | None, video_code: str | None) -> None:
    """推送成功后调用。检查开关，启用则 fire-and-forget 延迟任务。

    异常隔离：绝不影响 push 的成功状态。
    """
    try:
        cfg = _get_config()
        if not _to_bool(cfg.get("cd2_organize_enabled")):
            return  # 未启用，静默跳过
        if not task_id:
            logger.warning("[CD2迁移] 无 task_id，跳过（无法定位女优和文件名）")
            return
        try:
            delay = int(cfg.get("cd2_organize_delay_seconds", "") or "120")
        except ValueError:
            delay = 120
        if delay < 0:
            delay = 0
        asyncio.create_task(_do_organize(task_id, video_code, delay))
    except Exception as e:
        logger.warning(f"[CD2迁移] schedule_organize 异常（不影响推送）: {e}")


async def test_organize(config: dict) -> dict:
    """测试 CD2 迁移配置（供 downloaders.test_connection 的 cd2_organize 分支调用）。

    尝试列出源文件夹，返回文件数。验证 CD2 连接 + 路径配置正确。
    """
    from services.cd2_client import get_token_or_login, list_folder
    cd2_url = config.get("clouddrive_url", "").strip()
    source_folder = config.get("cd2_organize_source_folder", "").strip()
    if not cd2_url:
        return {"ok": False, "message": "未配置 clouddrive_url"}
    if not source_folder:
        return {"ok": False, "message": "未配置 cd2_organize_source_folder"}

    token, err = await get_token_or_login(config)
    if err:
        return {"ok": False, "message": err}

    files, list_err = await list_folder(cd2_url, token, source_folder)
    if list_err:
        return {"ok": False, "message": f"列目录失败: {list_err}"}
    video_count = sum(1 for f in files if not f.get("is_directory"))
    return {"ok": True, "message": f"源文件夹可达，含 {len(files)} 个条目（{video_count} 个文件）"}
