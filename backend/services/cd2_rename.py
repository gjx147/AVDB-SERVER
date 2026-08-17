"""CD2 下载文件整理 —— 推送成功后原地重命名 + 清理。

推送 CloudDrive2 成功后延迟触发：
1. CD2 GetSubFiles 列下载文件夹，定位 {番号} 子文件夹（或直接匹配的文件）
2. 子文件夹内：≥200MB 的视频文件重命名为 {番号}.{原扩展名}（多文件加 -2/-3）
3. 其余文件（<200MB 的剧照/txt 等垃圾）删除
不移动文件、不建目录 —— 全部原地处理。

settings 表 key：
- cd2_rename_enabled: 总开关
- cd2_download_folder: CD2 离线下载文件夹（整理范围）
- cd2_rename_delay_seconds: 延迟触发秒数（默认 300，等 CD2 下载）
复用 clouddrive_url/token/username/password（CD2 登录）。
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("avdb.downloaders.cd2")

SMALL_THRESHOLD = 200 * 1024 * 1024  # 200MB

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".wmv", ".ts", ".m2ts", ".mov", ".flv", ".webm", ".iso"}


def _get_config() -> dict[str, str]:
    from database import SessionLocal
    from models import Setting
    keys = [
        "cd2_rename_enabled", "cd2_download_folder", "cd2_rename_delay_seconds",
        "clouddrive_url", "clouddrive_token", "clouddrive_username", "clouddrive_password",
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


def _get_ext(name: str) -> str:
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


def _classify(entries: list[dict], video_code: str) -> tuple[list[str], list[tuple[str, str]]]:
    """把条目分成（待删除, 待重命名[(full_path, name)]）。

    - 目录：不动（保守）
    - ≥200MB 且是视频扩展名：待重命名
    - 其余文件（<200MB 或非视频）：待删除
    """
    small: list[str] = []
    big: list[tuple[str, str]] = []
    for f in entries:
        if f.get("is_directory"):
            continue
        full = f.get("full_path")
        name = f.get("name") or ""
        if not full:
            continue
        size = f.get("size", 0) or 0
        if size >= SMALL_THRESHOLD and _get_ext(name) in VIDEO_EXTS:
            big.append((full, name))
        else:
            small.append(full)
    return small, big


async def _process_scope(cd2_url: str, token: str, entries: list[dict], video_code: str, scope_desc: str) -> bool:
    """对一组条目执行 删除小文件 + 重命名大文件。返回是否处理了任何文件。"""
    small, big = _classify(entries, video_code)

    if small:
        ok, msg = await delete_files(cd2_url, token, small)
        if ok:
            logger.info(f"[CD2整理] 已删除 {len(small)} 个非视频/小文件 (<200MB) {scope_desc}")
        else:
            logger.warning(f"[CD2整理] 删除小文件失败（继续重命名）: {msg}")

    renamed = 0
    for idx, (full, name) in enumerate(big):
        ext = _get_ext(name)
        new_name = f"{video_code}{ext}" if idx == 0 else f"{video_code}-{idx + 1}{ext}"
        if name == new_name:
            renamed += 1
            continue
        ok, msg = await rename_file(cd2_url, token, full, new_name)
        if ok:
            renamed += 1
            logger.info(f"[CD2整理] 重命名: {name} → {new_name}")
        else:
            logger.warning(f"[CD2整理] 重命名失败 {name}→{new_name}: {msg}")

    logger.info(f"[CD2整理] {scope_desc} 完成: 重命名 {renamed}/{len(big)}，删除 {len(small)}")
    return bool(small or big)


async def _do_rename(task_id: int | None, video_code: str | None, delay: int):
    """延迟任务主体：列下载文件夹 → 找番号子文件夹（或直接文件）→ 原地整理。"""
    label = f"task_id={task_id} video_code={video_code or '无'}"
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        logger.info(f"[CD2整理] 延迟任务已取消 ({label})")
        raise

    cfg = _get_config()
    cd2_url = cfg.get("clouddrive_url", "").strip()
    folder = cfg.get("cd2_download_folder", "").strip()
    if not cd2_url or not folder:
        logger.warning(f"[CD2整理] 跳过：clouddrive_url / cd2_download_folder 未配置 ({label})")
        return
    if not video_code:
        logger.warning(f"[CD2整理] 跳过：无番号 ({label})")
        return

    from services.cd2_client import get_token_or_login, list_folder
    token, err = await get_token_or_login(cfg)
    if err:
        logger.error(f"[CD2整理] CD2 登录失败 ({label}): {err}")
        return

    entries, list_err = await list_folder(cd2_url, token, folder)
    if list_err:
        logger.error(f"[CD2整理] 列下载文件夹失败 ({label}): {list_err}")
        return

    vc_lower = video_code.lower()
    # 优先：{番号} 子文件夹（CD2 离线下载通常建同名目录）
    for f in entries:
        if f.get("is_directory") and vc_lower in (f.get("name") or "").lower() and f.get("full_path"):
            sub_entries, sub_err = await list_folder(cd2_url, token, f["full_path"])
            if sub_err:
                logger.error(f"[CD2整理] 列子文件夹 {f['full_path']} 失败 ({label}): {sub_err}")
                return
            await _process_scope(cd2_url, token, sub_entries, video_code, f"(子文件夹 {f['full_path']})")
            return

    # 其次：下载文件夹根下直接匹配番号的文件（无子文件夹的种子）
    matched = [f for f in entries if not f.get("is_directory") and vc_lower in (f.get("name") or "").lower()]
    if matched:
        await _process_scope(cd2_url, token, matched, video_code, "(根目录匹配文件)")
        return

    logger.info(f"[CD2整理] 未找到匹配 {video_code} 的子文件夹/文件，可能 CD2 还在下载 ({label})")


def schedule_rename(task_id: int | None, video_code: str | None) -> None:
    """CD2 推送成功后调用。检查开关，启用则 fire-and-forget 延迟任务。

    异常隔离：绝不影响 push 的成功状态。
    """
    try:
        cfg = _get_config()
        if not _to_bool(cfg.get("cd2_rename_enabled")):
            return
        if not task_id:
            logger.warning("[CD2整理] 无 task_id，跳过")
            return
        try:
            delay = int(cfg.get("cd2_rename_delay_seconds", "") or "300")
        except ValueError:
            delay = 300
        asyncio.create_task(_do_rename(task_id, video_code, max(0, delay)))
        logger.info(f"[CD2整理] 已计划 {delay}s 后整理 (task_id={task_id} {video_code})")
    except Exception as e:
        logger.warning(f"[CD2整理] schedule_rename 异常（不影响推送）: {e}")


async def test_rename(config: dict) -> dict:
    """测试配置（供 downloaders.test_connection 调用）：列下载文件夹验证可达。"""
    from services.cd2_client import get_token_or_login, list_folder
    cd2_url = config.get("clouddrive_url", "").strip()
    folder = config.get("cd2_download_folder", "").strip()
    if not cd2_url:
        return {"ok": False, "message": "未配置 clouddrive_url"}
    if not folder:
        return {"ok": False, "message": "未配置 cd2_download_folder"}
    token, err = await get_token_or_login(config)
    if err:
        return {"ok": False, "message": err}
    files, list_err = await list_folder(cd2_url, token, folder)
    if list_err:
        return {"ok": False, "message": f"列目录失败: {list_err}"}
    return {"ok": True, "message": f"下载文件夹可达，含 {len(files)} 个条目"}
