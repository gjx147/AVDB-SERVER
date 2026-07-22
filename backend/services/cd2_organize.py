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
import logging
import re

logger = logging.getLogger("avdb.downloaders.cd2")


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
    """识别主演女优名。

    策略（按可靠性优先）：
    1. 通过 actor_movies 关联表查 task 关联的演员，过滤 gender=female 取第一个
    2. 否则解析 task.actors 文本字段，查 actors 表过滤 gender=female
    3. 都查不到女优时回退 task.actors 文本第一个名字
    4. 完全无演员信息回退 '未分类'

    不依赖 task.actors 文本字段的顺序（第一个可能不是女优）。
    """
    try:
        from models import Actor, actor_movies
        from sqlalchemy import select

        # 1. 优先走关联表（更可靠：爬虫补齐演员作品时建立的关联）
        try:
            rows = db.execute(
                select(Actor)
                .join(actor_movies, actor_movies.c.actor_id == Actor.id)
                .where(actor_movies.c.task_id == task.id)
                .order_by(actor_movies.c.created_at)
            ).scalars().all()
            for r in rows:
                if (r.gender or "").lower() == "female":
                    return _sanitize_folder_name(r.name)
            # 关联表有演员但都不是 female，继续走文本字段
        except Exception as e:
            logger.warning(f"[CD2迁移] 关联表查询失败: {e}")

        # 2. 解析 task.actors 文本字段查 actors 表
        actors_str = (task.actors or "").strip()
        if actors_str:
            names = [n.strip() for n in actors_str.split(",") if n.strip()]
            if names:
                rows = db.execute(
                    select(Actor).where(Actor.name.in_(names)).order_by(Actor.id)
                ).scalars().all()
                # 优先返回 female
                for r in rows:
                    if (r.gender or "").lower() == "female":
                        return _sanitize_folder_name(r.name)
                # 文本字段查到了演员但都不是 female，回退用文本第一个名
                return _sanitize_folder_name(names[0])

    except Exception as e:
        logger.warning(f"[CD2迁移] 演员表查询异常: {e}")
        # 异常时尝试用文本字段兜底
        actors_str = (task.actors or "").strip()
        if actors_str:
            names = [n.strip() for n in actors_str.split(",") if n.strip()]
            if names:
                return _sanitize_folder_name(names[0])

    return "未分类"


def _find_video_code_subfolder(files: list[dict], video_code: str | None) -> dict | None:
    """从源文件夹列表里找匹配 video_code 的子文件夹。

    CD2 每次下载会生成 {video_code} 子文件夹。匹配规则：目录名包含 video_code（不区分大小写）。
    无 video_code 或没匹配到返回 None。
    """
    if not video_code:
        return None
    vc_lower = video_code.lower()
    for f in files:
        if not f.get("is_directory"):
            continue
        name = (f.get("name") or "").lower()
        if vc_lower in name:
            return f
    return None


def _get_ext(name: str) -> str:
    """取文件扩展名（小写，含点）。无扩展返回 ''。"""
    if "." in name:
        return "." + name.rsplit(".", 1)[-1].lower()
    return ""


async def _do_organize(task_id: int, video_code: str | None, delay: int):
    """延迟任务主体。

    流程：
    1. sleep(delay) 等待 CD2 下载完成
    2. 在源文件夹下找 {video_code} 子文件夹
    3. 进入子文件夹：
       - <200MB 的文件删除（清理剧照/txt 等垃圾）
       - ≥200MB 的文件重命名为 {video_code}.{原扩展名}（多文件加序号 -2/-3...）
    4. 在媒体库建 {女优名} 子目录（幂等）
    5. 把子文件夹里所有剩余文件移动到 /媒体库/{女优名}/
    6. 可选：删除空的子文件夹
    7. 可选：通知 CMS auto_organize（若 cms_enabled）
    """
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
    if not video_code:
        logger.warning(f"[CD2迁移] 跳过：无 video_code 无法定位子文件夹 ({label})")
        return

    # 查 Task 拿女优名
    from database import SessionLocal
    from models import Task
    db = SessionLocal()
    try:
        task = db.get(Task, task_id) if task_id else None
        actress_name = _get_lead_actress_name(db, task) if task else "未分类"
        logger.info(f"[CD2迁移] 开始 ({label}): 女优={actress_name}")
    finally:
        db.close()

    # CD2 登录
    from services.cd2_client import (
        get_token_or_login, list_folder, create_folder, move_file,
        rename_file, delete_files,
    )
    token, err = await get_token_or_login(cfg)
    if err:
        logger.error(f"[CD2迁移] CD2 登录失败 ({label}): {err}")
        return

    # 1. 在源文件夹下找 {video_code} 子文件夹
    entries, list_err = await list_folder(cd2_url, token, source_folder)
    if list_err:
        logger.error(f"[CD2迁移] 列源文件夹失败 ({label}): {list_err}")
        return
    sub = _find_video_code_subfolder(entries, video_code)
    if not sub or not sub.get("full_path"):
        logger.info(f"[CD2迁移] 源文件夹下未找到匹配 {video_code} 的子文件夹，可能 CD2 还在下载 ({label})")
        return
    sub_path = sub["full_path"]
    logger.info(f"[CD2迁移] 找到子文件夹: {sub_path}")

    # 2. 列子文件夹内容
    sub_entries, sub_err = await list_folder(cd2_url, token, sub_path)
    if sub_err:
        logger.error(f"[CD2迁移] 列子文件夹 {sub_path} 失败 ({label}): {sub_err}")
        return
    logger.info(f"[CD2迁移] 子文件夹 {sub_path} 共 {len(sub_entries)} 个条目")

    # 3. 分类处理：<200MB 删除，≥200MB 重命名
    SMALL_THRESHOLD = 200 * 1024 * 1024  # 200MB
    small_files = []  # 待删除
    big_files = []     # 待重命名
    for f in sub_entries:
        if f.get("is_directory"):
            continue  # 子文件夹里的子目录不动（保守）
        full = f.get("full_path")
        if not full:
            continue
        size = f.get("size", 0) or 0
        if size < SMALL_THRESHOLD:
            small_files.append(full)
        else:
            big_files.append((full, f.get("name", "")))

    # 删除小文件
    if small_files:
        ok, msg = await delete_files(cd2_url, token, small_files)
        if ok:
            logger.info(f"[CD2迁移] 已删除 {len(small_files)} 个小文件 (<200MB)")
        else:
            logger.warning(f"[CD2迁移] 删除小文件失败（继续处理）: {msg}")
    else:
        logger.info(f"[CD2迁移] 无需删除小文件")

    # 重命名大文件 → {video_code}.{ext}（多文件加序号）
    renamed_full_paths = []
    for idx, (full, name) in enumerate(big_files):
        ext = _get_ext(name)
        if idx == 0:
            new_name = f"{video_code}{ext}"
        else:
            new_name = f"{video_code}-{idx + 1}{ext}"
        # 已是目标名则跳过
        if name == new_name:
            renamed_full_paths.append(full)
            continue
        ok, msg = await rename_file(cd2_url, token, full, new_name)
        if ok:
            # 重命名后 full_path 的文件名部分变了，重新拼接
            parent = full.rsplit("/", 1)[0] if "/" in full else ""
            new_full = f"{parent}/{new_name}" if parent else new_name
            renamed_full_paths.append(new_full)
            logger.info(f"[CD2迁移] 重命名: {name} → {new_name}")
        else:
            logger.warning(f"[CD2迁移] 重命名失败 {name}→{new_name}（用原名移动）: {msg}")
            renamed_full_paths.append(full)

    if not renamed_full_paths:
        logger.warning(f"[CD2迁移] 子文件夹内无大文件可移动 ({label})")
        return

    # 4. 媒体库建女优子目录（幂等）
    dest_path = f"{target_folder.rstrip('/')}/{actress_name}"
    ok, msg = await create_folder(cd2_url, token, target_folder, actress_name)
    if not ok:
        logger.error(f"[CD2迁移] 创建目录 {dest_path} 失败 ({label}): {msg}")
        return
    logger.info(f"[CD2迁移] 目录就绪: {dest_path}")

    # 5. 移动重命名后的大文件到女优目录
    ok, msg = await move_file(cd2_url, token, renamed_full_paths, dest_path)
    if ok:
        logger.info(f"[CD2迁移] 移动成功 ({label}): {len(renamed_full_paths)} 个文件 → {dest_path}")
    else:
        logger.error(f"[CD2迁移] 移动失败 ({label}): {msg}")
        return

    # 6. 可选：更新 Task.media_in_library
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

    # 7. 可选：通知 CMS 入库
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
