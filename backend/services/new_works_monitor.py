"""新作品监控服务 —— 检测订阅演员的新作品。

完整流程：
1. 用浏览器池抓演员作品页（/search?q={name}&f=actor）
2. 解析作品列表（番号 + 标题 + 详情链接 + 封面）
3. 与 tasks 表已有番号 + new_releases 表去重
4. 新作品写入 new_releases 表
5. Emby 比对：已在媒体库的标记跳过；不在库的才是真正新作
6. 真正新作 → 发通知（notify new_works）
7. auto_add=True → 创建 task + 触发 scraper 爬详情 + 延迟 push 下载

设计：
- async（挂 APScheduler 或 subscription_monitor 调用）
- 浏览器池抓取 + BeautifulSoup 解析
- 番号比对去重 + Emby 在库比对
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from sqlalchemy import select

from config import get_settings
from database import SessionLocal
from models import Actor, Download, ListSource, NewRelease, Task

logger = logging.getLogger("avdb.new_works")

# 从标题/链接提取番号
_CODE_RE = re.compile(r"([A-Za-z]{2,6})[-_]?(\d{2,5})")


def _extract_code(text: str) -> str | None:
    m = _CODE_RE.search(text)
    if m:
        return f"{m.group(1).upper()}-{m.group(2)}"
    return None


def _get_db_setting(key: str) -> str | None:
    """从 DB settings 表读配置（http_proxy / javdb_url 等）。
    scraper 子进程读 env，所以启动前必须把 DB 配置注入 env。
    """
    from database import SessionLocal
    from models import Setting
    db = SessionLocal()
    try:
        row = db.get(Setting, key)
        return row.value if row else None
    finally:
        db.close()


def _scraper_path() -> Path:
    """scraper 脚本绝对路径：相对项目根解析（对齐 routers/crawl.py），
    兼容 Docker（/app/magnet_scraper/scraper.py）与非 Docker 部署。"""
    return Path(__file__).resolve().parent.parent.parent / "magnet_scraper" / "scraper.py"


async def _trigger_crawl_actor(actor_name: str, actor_url: str = "", actor_id: int | None = None) -> bool:
    """触发 scraper 子进程 crawl-actor（跟演员库"补齐作品"完全一样）。

    有 actor_url → crawl-actor --actor-url（直接爬详情页+翻页作品）
    无 actor_url → crawl-actor --actor-name（搜索+爬详情+入库+写source_url）
    actor_id → 传 --actor-id，按 id 关联作品，杜绝名字匹配建重复演员。
    注册 scraper_lock 防止并发 Chromium 冲突，同步等待完成（最多 300s）。
    """
    from services import scraper_lock
    if scraper_lock.is_running():
        logger.warning("[新作监控] scraper 忙，跳过 crawl-actor")
        return False

    _scraper = str(_scraper_path())
    if actor_url:
        cmd = [sys.executable, _scraper,
               "crawl-actor", "--actor-url", actor_url]
    else:
        cmd = [sys.executable, _scraper,
               "crawl-actor", "--actor-name", actor_name]
    if actor_id is not None:
        cmd += ["--actor-id", str(actor_id)]
    cmd += ["--no-extract"]  # 巡检只检测新作，不提取详情（避免长时间阻塞 + 300s 超时）
    logger.info(f"[新作监控] 启动 scraper 子进程: {' '.join(cmd[-4:])}...")

    # 关键修复：从 DB settings 读 http_proxy + javdb_url 注入子进程 env
    # （与 routers/crawl.py 的 _start_scraper 一致，否则 scraper 的
    #   os.environ.get("HTTP_PROXY") 为空，Chromium 无代理被 Cloudflare 拦截）
    env = dict(os.environ)
    _proxy = _get_db_setting("http_proxy")
    if _proxy:
        env["HTTP_PROXY"] = _proxy
        env["HTTPS_PROXY"] = _proxy
        env["http_proxy"] = _proxy
        env["https_proxy"] = _proxy
        logger.info(f"[新作监控] 注入代理到 scraper env: {_proxy}")
    _javdb_url = _get_db_setting("javdb_url")
    if _javdb_url:
        env["JAVDB_URL"] = _javdb_url

    # Phase 2 F07：注入回调共享密钥（register/unregister 回调鉴权）
    env["SCRAPER_CALLBACK_TOKEN"] = scraper_lock.get_callback_token()

    # P0 修复: stdout/stderr 落盘日志文件（不 PIPE，避免管道写满死锁 + 不阻塞事件循环）。
    # 命名/位置对齐 routers/crawl.py 的 scraper_stderr.log 风格（DATA_DIR 下），追加模式。
    log_path = Path(get_settings().DATA_DIR) / "scraper_actor_crawl.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "a", encoding="utf-8")
    except Exception as e:
        log_file = None
        logger.warning(f"[新作监控] 打开日志文件失败({log_path})，scraper 输出将丢弃: {e}")

    popen_kwargs: dict = {
        "env": env,
        "cwd": str(_scraper_path().parent),
        "stdout": log_file if log_file is not None else asyncio.subprocess.DEVNULL,
        "stderr": asyncio.subprocess.STDOUT,  # stderr 合并到 stdout 同一落盘文件
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, **popen_kwargs)
        # 注册 scraper_lock 防止其他路径并发启动 Chromium
        scraper_lock.set_proc(proc, {"mode": "crawl-actor", "pid": proc.pid})
        logger.info(f"[新作监控] scraper crawl-actor 输出落盘: {log_path}")
        try:
            await asyncio.wait_for(proc.wait(), timeout=300)
        except asyncio.TimeoutError:
            _kill_process_tree(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except Exception:
                pass
            logger.warning(f"[新作监控] scraper crawl-actor 超时(300s)，已杀进程树: {actor_name}")
            return False
        if proc.returncode == 0:
            logger.info(f"[新作监控] scraper crawl-actor 完成: {actor_name}")
            return True
        else:
            logger.warning(f"[新作监控] scraper crawl-actor 失败(rc={proc.returncode})，日志见 {log_path}")
            return False
    except Exception as e:
        logger.warning(f"[新作监控] scraper crawl-actor 异常: {e}")
        return False
    finally:
        if log_file is not None:
            try:
                log_file.close()
            except Exception:
                pass
        # 仅当锁仍指向本进程才 clear（防 ABA：避免误清别人新持有的锁）
        if proc is not None and scraper_lock.get_proc() is proc:
            scraper_lock.clear()


def _kill_process_tree(proc) -> None:
    """杀整个进程树（包括 Playwright Chromium 子进程）。对齐 auto_crawl._kill_process_tree。"""
    if proc.returncode is not None:
        return  # 已退出
    pid = proc.pid
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


async def _get_actor_works_from_db(db, actor_id: int) -> list[dict]:
    """从 DB 读演员已关联的作品（crawl-actor 入库后），返回 [{code, title, url, cover}]。

    用于 crawl-actor 补齐后跳过 _fetch_actor_works 直接对比。
    """
    from models import actor_movies
    rows = db.execute(
        select(Task.video_code, Task.title, Task.url, Task.thumbnail_urls)
        .join(actor_movies, actor_movies.c.task_id == Task.id)
        .where(actor_movies.c.actor_id == actor_id)
        .order_by(Task.id.desc())
    ).all()
    works = []
    for video_code, title, url, thumbnail_urls in rows:
        if not video_code:
            continue
        cover = None
        if thumbnail_urls:
            try:
                import json
                arr = json.loads(thumbnail_urls)
                if isinstance(arr, list) and arr:
                    cover = arr[0]
            except Exception:
                pass
        works.append({"code": video_code, "title": title or "", "url": url or "", "cover": cover})
    return works




async def check_actor_new_works(actor_id: int, subscription_id: int | None = None,
                                 auto_add: bool = False, skip_crawl: bool = False) -> dict:
    """检测某演员的新作品。返回摘要。

    流程：抓 javdb → 去重 → 写 new_releases → Emby 比对 → 通知 → 可选自动下载。
    """
    db = SessionLocal()
    try:
        actor = db.get(Actor, actor_id)
        if not actor:
            logger.warning(f"[新作监控] 演员不存在 actor_id={actor_id}")
            return {"error": "演员不存在", "actor_id": actor_id}
        if not actor.name:
            logger.warning(f"[新作监控] 演员无名字 actor_id={actor_id}")
            return {"error": "演员无名字", "actor_id": actor_id}

        logger.info(f"[新作监控] 开始检查演员 {actor.name} (id={actor_id}, auto_add={auto_add})")

        # 统一逻辑：每次巡检都调 scraper crawl-actor 子进程（跟"补齐作品"完全一样）
        # source_url 有值 → crawl-actor --actor-url（翻页爬最新作品列表）
        # source_url 为空 → crawl-actor --actor-name（搜索+爬详情+入库+写source_url）
        actor_url = actor.source_url or ""
        if not actor_url:
            note = actor.note or ""
            if note.startswith("source_url: "):
                actor_url = note.split(":", 1)[1].strip()

        if skip_crawl:
            logger.info(f"[新作监控] {actor.name} 跳过爬取（刚补齐过），直接对比入库")
        else:
            logger.info(f"[新作监控] {actor.name} 触发 scraper crawl-actor ({actor_url or '按名字搜索'})")
            ok = await _trigger_crawl_actor(actor.name, actor_url, actor_id=actor_id)
            if not ok:
                return {"type": "actor", "actor_id": actor_id, "error": "scraper crawl-actor 失败或被占用"}

        # crawl-actor 完成后，提交当前事务以获取新快照：scraper 子进程在 WAL 下提交的
        # 新 actor_movies 对本会话不可见（快照隔离），仅 expire_all 不够，必须结束事务重开。
        db.commit()
        works = await _get_actor_works_from_db(db, actor_id)
        logger.info(f"[新作监控] {actor.name}: DB 读到 {len(works)} 部作品")

        # 去重：已有 task 的 + 已在 new_releases 的
        existing_codes: set[str] = set()
        for r in db.execute(select(Task.video_code).where(Task.video_code.isnot(None))).all():
            existing_codes.add(r[0])
        for r in db.execute(
            select(NewRelease.video_code).where(NewRelease.actor_id == actor_id)
        ).all():
            existing_codes.add(r[0])

        new_works = [w for w in works if w["code"] not in existing_codes]
        added_releases: list[NewRelease] = []
        for w in new_works:
            nr = NewRelease(
                actor_id=actor_id,
                video_code=w["code"],
                title=w["title"],
                detail_url=w["url"],
                cover_url=w["cover"],
            )
            db.add(nr)
            added_releases.append(nr)
        db.flush()  # 拿到 nr.id

        # ── Emby 比对：已在媒体库的标记跳过 ──
        from services.media_server import check_in_library

        truly_new: list[NewRelease] = []  # Emby 不在库的真正新作
        in_library_count = 0
        for nr in added_releases:
            try:
                in_lib = await check_in_library(nr.video_code)
            except Exception as e:
                logger.warning(f"Emby 查询 {nr.video_code} 失败（视为不在库）: {e}")
                in_lib = False
            if in_lib:
                nr.added_to_library = True
                nr.is_read = True  # 已在库，标记已读
                in_library_count += 1
            else:
                truly_new.append(nr)
        db.commit()

        # ── 发通知（有真正新作才发）──
        if truly_new:
            from services.notifier import notify
            codes = [nr.video_code for nr in truly_new]
            try:
                # N10: 附同厂牌已收藏高分作品（相似推荐）
                _rec = ""
                try:
                    _first_maker = db.execute(
                        select(Task.maker).where(Task.video_code.in_(codes), Task.maker.isnot(None))
                    ).scalars().first()
                    if _first_maker:
                        _favs = db.execute(
                            select(Task.video_code, Task.rating)
                            .where(Task.maker == _first_maker, Task.rating.isnot(None),
                                   Task.view_status.in_(["viewed", "want"]))
                            .order_by(Task.rating.desc()).limit(3)
                        ).all()
                        if _favs:
                            _rec = "\n相似收藏: " + " / ".join(f"{c}({r})" for c, r in _favs)
                except Exception:
                    pass
                await notify(
                    "new_works",
                    f"{actor.name} 新作品",
                    f"新增 {len(truly_new)} 部: {', '.join(codes[:5])}{_rec}"
                    + ("…" if len(codes) > 5 else ""),
                )
            except Exception as e:
                logger.warning(f"通知发送失败（不影响主流程）: {e}")

        # ── 自动入库：新作默认自动建 task 入影片库（无需手动点入库）──
        if truly_new:
            for nr in truly_new:
                try:
                    task_id = add_to_library(nr.id, db)
                    if task_id:
                        nr.task_id = task_id
                        nr.added_to_library = True
                except Exception as e:
                    logger.warning(f"新作自动入库失败 {nr.video_code}: {e}")
            db.commit()

        # ── auto_add：自动入库的基础上，额外抓磁力 + 自动推送下载 ──
        pushed = 0
        if auto_add and truly_new:
            for nr in truly_new:
                if not nr.task_id:
                    continue
                try:
                    await _trigger_extract_and_push(nr.task_id, nr.video_code)
                    pushed += 1
                except Exception as e:
                    logger.warning(f"自动下载 {nr.video_code} 失败: {e}")

        total_unread = len(db.execute(
            select(NewRelease).where(
                NewRelease.actor_id == actor_id,
                NewRelease.is_read == False,  # noqa: E712
            )
        ).scalars().all())

        return {
            "type": "actor",
            "actor_id": actor_id,
            "actor_name": actor.name,
            "scanned": len(works),
            "new_count": len(added_releases),
            "in_library": in_library_count,
            "truly_new": len(truly_new),
            "pushed": pushed,
            "total_unread": total_unread,
        }
    finally:
        db.close()


async def _trigger_extract_and_push(task_id: int, video_code: str) -> None:
    """为新入库的 task 触发 scraper 爬详情拿磁力，延迟自动 push 下载。

    task 已由「自动入库」（add_to_library）创建，这里只补磁力抓取与下载推送。
    """
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if not task:
            return
        task_url = task.url or f"pending://{video_code}"
    finally:
        db.close()

    # 触发 scraper extract-single（subprocess，非阻塞 fire-and-forget）
    import sys as _sys, os as _os
    from services import scraper_lock as _lock
    if _lock.is_running():
        # P1 修复：锁忙时把 task 标记 failed，让 auto_retry 的 extract --failed-only 接管重试
        # （原"排队"只是日志——pending 任务不会被 auto_retry 处理，磁力抓取会静默丢失）
        logger.info(f"[新作监控] scraper 忙，task {task_id} 标记 failed 交 auto_retry 重试")
        _db2 = SessionLocal()
        try:
            _t2 = _db2.get(Task, task_id)
            if _t2:
                _t2.status = "failed"
                _db2.commit()
        finally:
            _db2.close()
    else:
        try:
            # 关键修复：注入 http_proxy + javdb_url 到子进程 env（同 _trigger_crawl_actor）
            _env = dict(_os.environ)
            _proxy = _get_db_setting("http_proxy")
            if _proxy:
                _env["HTTP_PROXY"] = _proxy
                _env["HTTPS_PROXY"] = _proxy
                _env["http_proxy"] = _proxy
                _env["https_proxy"] = _proxy
            _javdb_url = _get_db_setting("javdb_url")
            if _javdb_url:
                _env["JAVDB_URL"] = _javdb_url
            # Phase 2 F07：注入回调共享密钥（register/unregister 回调鉴权）
            _env["SCRAPER_CALLBACK_TOKEN"] = _lock.get_callback_token()
            proc = await asyncio.create_subprocess_exec(
                _sys.executable, str(_scraper_path()),
                "extract-single", "--url", task_url,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=_env,
            )
            # 注册 scraper_lock 防止并发
            _lock.set_proc(proc, {"mode": "extract-single", "pid": proc.pid, "auto": True})
            logger.info(f"[新作监控] 触发 scraper extract-single task {task_id} (pid={proc.pid})")
            # 启动后台任务等 proc 完成后 clear lock
            asyncio.create_task(_wait_and_clear_lock(proc))
        except Exception as e:
            logger.warning(f"[新作监控] 触发 scraper 失败（task {task_id} 保留 pending）: {e}")

    # 延迟检查 task 是否拿到磁力，有则自动 push
    asyncio.create_task(_delayed_push_if_ready(task_id, video_code, delay=180))


async def _wait_and_clear_lock(proc) -> None:
    """等 proc 完成后清除 scraper_lock（防止 extract-single 子进程长期占锁）。

    Phase 2 P1-3：clear 前按身份判断（get_proc() is proc），防 ABA——
    避免本进程退出时清掉并发新持有的锁。
    """
    try:
        await proc.wait()
    except Exception:
        pass
    finally:
        from services import scraper_lock
        if scraper_lock.get_proc() is proc:
            scraper_lock.clear()


_push_semaphore = asyncio.Semaphore(3)  # 推送并发削峰：同轮巡检的多个推送任务最多 3 个并发（防连接池风暴）


async def _delayed_push_if_ready(task_id: int, video_code: str, delay: int = 180):
    """延迟 N 秒后检查 task 是否爬完拿到磁力，有则自动 push 下载。

    scraper extract-single 是异步 subprocess，需要等它爬完。180 秒后检查，
    如果还没磁力就跳过（auto_retry 会后续重试）。
    并发削峰：信号量 3——等待延迟不占坑，实际推送阶段才受限。
    """
    await asyncio.sleep(delay)
    async with _push_semaphore:
        db = SessionLocal()
        try:
            task = db.get(Task, task_id)
            if not task or not task.best_magnet:
                logger.info(
                    f"[新作监控] task {task_id} {video_code} 暂无磁力（scraper 可能未完成），跳过自动 push"
                )
                return

            # 推送前复核 Emby：建任务到推送之间有 180s 窗口，作品可能刚进媒体库
            try:
                from services.media_server import check_in_library
                in_lib = await check_in_library(video_code)
                if in_lib is True:
                    logger.info(f"[新作监控] 推送前复核：{video_code} 已在 Emby 媒体库，跳过自动 push")
                    return
            except Exception as e:
                logger.warning(f"[新作监控] 推送前 Emby 复核失败（继续推送）: {e}")

            # 读下载器配置
            from routers.downloaders import _get_setting, _push_clouddrive, _push_qbittorrent, _extract_hash
            config_keys = [
                "qb_url", "qb_username", "qb_password", "qbittorrent_save_path",
                "clouddrive_url", "clouddrive_token", "clouddrive_username",
                "clouddrive_password", "clouddrive_save_path",
            ]
            config = {k: _get_setting(db, k) for k in config_keys}
            downloader = _get_setting(db, "default_downloader") or "qbittorrent"

            logger.info(f"[新作监控] 自动推送 {video_code} 到 {downloader}")
            if downloader == "clouddrive":
                result = await _push_clouddrive(task.best_magnet, config)
            else:
                result = await _push_qbittorrent(task.best_magnet, config)

            if result.get("ok"):
                # 记录 download
                dl = Download(
                    task_id=task_id,
                    video_code=video_code,
                    magnet=task.best_magnet,
                    info_hash=_extract_hash(task.best_magnet),
                    downloader=downloader,
                    status="pushed",
                )
                db.add(dl)
                db.commit()
                logger.info(f"[新作监控] 自动推送 {video_code} 成功")
                # CD2 推送成功 → 延迟整理（开关/延迟在设置页；异常隔离不影响推送）
                if downloader == "clouddrive":
                    try:
                        from services.cd2_rename import schedule_rename
                        schedule_rename(task_id, video_code)
                    except Exception as e:
                        logger.warning(f"[CD2整理] 调度失败（不影响推送）: {e}")
            else:
                logger.warning(f"[新作监控] 自动推送 {video_code} 失败: {result.get('message')}")
        except Exception as e:
            logger.error(f"[新作监控] _delayed_push_if_ready 异常 ({video_code}): {e}")
        finally:
            db.close()


async def run_check_all(auto_add: bool = False) -> dict:
    """对所有关注/订阅的演员执行新作品检测。

    关注与订阅现已统一为 actor 订阅，故只查 Subscription(actor, enabled)。
    """
    logger.info(f"[新作监控] 开始批量巡检 (auto_add={auto_add})")
    db = SessionLocal()
    try:
        from models import Subscription

        sub_actors = db.execute(
            select(Actor).where(Actor.id.in_(
                select(Subscription.actor_id).where(
                    Subscription.sub_type == "actor",
                    Subscription.enabled == True,  # noqa: E712
                )
            ))
        ).scalars().all()
        actor_ids = {a.id for a in sub_actors}
        logger.info(f"[新作监控] 订阅演员 {len(sub_actors)} 个待检查")
    finally:
        db.close()

    results = []
    total_new = 0
    total_pushed = 0
    for aid in actor_ids:
        try:
            r = await check_actor_new_works(aid, auto_add=auto_add)
        except Exception as e:
            # 单个演员失败（如巡检进行中该演员被合并/删除）不中断整轮巡检（审查 A4）
            logger.warning(f"[新作监控] 演员 {aid} 巡检失败，跳过: {e}")
            results.append({"actor_id": aid, "error": str(e)})
            continue
        results.append(r)
        total_new += r.get("truly_new", r.get("new_count", 0))
        total_pushed += r.get("pushed", 0)
    return {
        "ok": True,
        "checked_actors": len(actor_ids),
        "total_new": total_new,
        "total_pushed": total_pushed,
        "results": results,
    }


def mark_read(new_release_id: int, db) -> bool:
    """标记新作品为已读。"""
    nr = db.get(NewRelease, new_release_id)
    if nr:
        nr.is_read = True
        return True
    return False


def add_to_library(new_release_id: int, db) -> int | None:
    """把新作品入库为 pending task。返回 task_id。"""
    nr = db.get(NewRelease, new_release_id)
    if not nr or nr.added_to_library:
        return nr.task_id if nr else None
    src = db.execute(
        select(ListSource).where(ListSource.list_code == "RANKING")
    ).scalar_one_or_none()
    if not src:
        src = ListSource(list_code="RANKING", list_path="/rankings")
        db.add(src)
        db.flush()
    task_url = nr.detail_url or f"/v/{nr.video_code}"
    # P2 修复：同一作品可能被多个演员订阅同时检出，url 唯一约束先查重复用 task
    existing = db.execute(select(Task).where(Task.url == task_url)).scalar_one_or_none()
    if existing:
        nr.added_to_library = True
        nr.task_id = existing.id
        nr.is_read = True
        return existing.id
    t = Task(list_source_id=src.id, url=task_url, video_code=nr.video_code)
    db.add(t)
    db.flush()
    nr.added_to_library = True
    nr.task_id = t.id
    nr.is_read = True
    return t.id
