"""女优资料双源聚合器 —— minnano-av + laoshi.ink。

全自动定时任务与手动重试共用。全部走 httpx 纯请求（无浏览器/无 CF 对抗），
代理从 DB settings 的 http_proxy 读（与 browser_pool 同款）。

稳定性设计（针对 NAS 链路的 SSL 抖动）：
- 每次请求自动重试 3 次（换新连接），最后一次改走直连（不经代理）兜底；
- laoshi 全量 JSON 落盘缓存（data/laoshi_jav.json），下载一次终身可用，
  之后只在缓存超过 24h 时后台刷新，刷新失败不影响现有数据；
- 三源回退逻辑（用户指定）：minnano-av（个人信息）→ laoshi.ink（百科维度），
  两源都支持中文名/英文名各试一遍。
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("avdb.actor_profile")

# 各源单请求超时
_TIMEOUT = 15.0

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _get_proxy() -> Optional[str]:
    """从 DB settings 读 http_proxy（与 browser_pool 同款），回退环境变量，无则 None。"""
    try:
        from database import SessionLocal
        from models import Setting
        db = SessionLocal()
        try:
            row = db.get(Setting, "http_proxy")
            if row and row.value and row.value.strip():
                return row.value.strip()
        finally:
            db.close()
    except Exception:
        pass
    import os
    return (os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or "").strip() or None


def _get_retry(url: str, params: dict | None = None, tries: int = 3) -> httpx.Response:
    """带重试的 GET：SSL/TCP 被掐断（SSL EOF、连接重置等）时换全新连接重试，
    最后一次改走直连（不经代理）兜底——代理隧道抖动是 NAS 上最常见的失败原因。"""
    last_err: Exception | None = None
    for i in range(tries):
        use_proxy = i < tries - 1  # 前两次走代理，最后一次直连
        try:
            with httpx.Client(timeout=_TIMEOUT, follow_redirects=True,
                              proxy=_get_proxy() if use_proxy else None,
                              headers={"User-Agent": _UA}) as c:
                r = c.get(url, params=params)
                r.raise_for_status()
                return r
        except Exception as e:
            last_err = e
            logger.info(f"请求失败（第{i + 1}/{tries} 次，{'代理' if use_proxy else '直连'}）{url}: {e}")
            time.sleep(0.6 * (i + 1))
    raise last_err  # type: ignore[misc]


# ── 源 1：minnano-av ──
def fetch_minnano(name: str) -> dict:
    """minnano-av：搜索（search_word）→ 详情页资料行（生日/三围/罩杯/身高/血型/星座）。

    精确匹配时搜索会直接跳到资料页；否则是结果列表，跟第一个 actressNNN.html。
    注意：判断依据是「页面是否含资料行标记」（birthday= / T-B-W-H 行）——
    结果列表里也会出现搜索词本身，仅凭名字在不在页面里判断会把列表误当资料页。
    """
    fields: dict = {}
    try:
        r = _get_retry("https://www.minnano-av.com/search_result.php", {
            "search_scope": "actress", "search_word": name,
        })
        html = r.text
        # 页面无资料行标记 → 仍是结果列表，跟进第一个演员详情链接
        if not re.search(r"birthday=|T[\d.]+\s*/\s*B[\d.]+", html):
            m = re.search(r"(actress\d+\.html)", html)
            if not m:
                logger.info(f"minnano 无结果 {name}")
                return fields
            html = _get_retry(f"https://www.minnano-av.com/{m.group(1)}").text
        # 资料行：T158 / B96(Gカップ) / W56 / H82
        m = re.search(r"T([\d.]+)\s*/\s*B([\d.]+)(?:\(([A-Z])?\))?", html)
        if m:
            fields["height"] = f"{m.group(1)}cm"
            if m.group(3):
                fields["cup"] = m.group(3)
            m2 = re.search(r"B[\d.]+\s*/\s*W([\d.]+)\s*/\s*H([\d.]+)", html)
            if m2:
                fields["measurements"] = f"B{m.group(2)} / W{m2.group(1)} / H{m2.group(2)}"
        # 生日
        m = re.search(r"birthday=(\d{4}-\d{2}-\d{2})", html)
        if m:
            y, mo, d = m.group(1).split("-")
            fields["birth_date"] = f"{y}年{int(mo):02d}月{int(d):02d}日"
        # 血型/星座
        m = re.search(r"(A型|B型|O型|AB型)", html)
        if m:
            fields["blood_type"] = m.group(1)
        m = re.search(r"(牡羊座|金牛座|双子座|巨蟹座|狮子座|处女座|天秤座|天蝎座|射手座|摩羯座|水瓶座|双鱼座)", html)
        if m:
            fields["zodiac"] = m.group(1)
        # 头像：详情页缩略图（p_actress_* 路径，去 query 后缀）
        m = re.search(r"src=\"(/p_actress_[^\"?]+)", html)
        if m:
            fields["avatar_url"] = "https://www.minnano-av.com" + m.group(1)
        return fields
    except Exception as e:
        logger.info(f"minnano 抓取失败 {name}: {e}")
        return fields


# ── 源 2：laoshi.ink ──
_LAOSHI_DATA: list[dict] | None = None  # 全量 JSON 进程内缓存（600 演员）
_LAOSHI_FAIL_AT: float = 0.0            # 上次下载失败时间（冷却 5 分钟重试）
_LAOSHI_CACHE_AGE = 86400               # 磁盘缓存超过 24h 后后台刷新


def _laoshi_cache_path() -> Path:
    from config import get_settings
    return Path(get_settings().DATA_DIR) / "laoshi_jav.json"


def _laoshi_read_cache() -> list[dict] | None:
    """读磁盘缓存（data/laoshi_jav.json），随 data 卷跨容器重启持久化。"""
    try:
        p = _laoshi_cache_path()
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        lst = None
        if isinstance(data, dict):
            lst = data.get("rankingActors") or data.get("actors")
        elif isinstance(data, list):
            lst = data
        return lst if isinstance(lst, list) and lst else None
    except Exception:
        return None


def _laoshi_write_cache(raw: object) -> None:
    try:
        p = _laoshi_cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        logger.info(f"laoshi 磁盘缓存写入失败: {e}")


def _laoshi_refresh_background(current: list[dict]) -> None:
    """后台刷新 laoshi 数据（daemon 线程）：成功则替换缓存，失败保留旧数据。"""
    import threading

    def _work():
        try:
            r = _get_retry("https://laoshi.ink/assets/data/light/jav.json")
            data = r.json()
            lst = data.get("rankingActors") or data.get("actors") or []
            if lst:
                global _LAOSHI_DATA
                _LAOSHI_DATA = lst
                _laoshi_write_cache(data)
                logger.info(f"laoshi 数据后台刷新完成: {len(lst)} 条")
            else:
                logger.info("laoshi 后台刷新：响应为空，保留旧数据")
        except Exception as e:
            logger.info(f"laoshi 后台刷新失败（保留旧数据）: {e}")

    threading.Thread(target=_work, daemon=True).start()


def _laoshi_json() -> list[dict]:
    """laoshi 全量数据：磁盘缓存优先（零网络），无缓存才下载；缓存超龄后台刷新。"""
    global _LAOSHI_DATA, _LAOSHI_FAIL_AT
    if _LAOSHI_DATA is not None:
        return _LAOSHI_DATA

    cached = _laoshi_read_cache()
    if cached is not None:
        _LAOSHI_DATA = cached
        try:
            age = time.time() - _laoshi_cache_path().stat().st_mtime
            if age > _LAOSHI_CACHE_AGE:
                _laoshi_refresh_background(cached)
        except Exception:
            pass
        return _LAOSHI_DATA

    if time.time() - _LAOSHI_FAIL_AT < 300:
        return []  # 冷却期内不重复请求
    try:
        r = _get_retry("https://laoshi.ink/assets/data/light/jav.json")
        data = r.json()
        lst = data.get("rankingActors") or data.get("actors") or []
        if lst:
            _laoshi_write_cache(data)
            _LAOSHI_DATA = lst
            _LAOSHI_FAIL_AT = 0.0
            logger.info(f"laoshi 数据已下载并落盘缓存: {len(lst)} 条")
        else:
            logger.info("laoshi 数据下载成功但内容为空")
    except Exception as e:
        logger.info(f"laoshi 数据加载失败（5 分钟后自动重试）: {e}")
        _LAOSHI_FAIL_AT = time.time()
        _LAOSHI_DATA = None
    return _LAOSHI_DATA or []


def fetch_laoshi(name: str) -> dict:
    """laoshi.ink：全量 JSON 匹配（author==名字），取出道年份/简介/活跃年限。

    简介来自 JSON description；出道年份 debut_year；活跃年限 = 今年 - 出道年份。
    """
    fields: dict = {}
    try:
        items = _laoshi_json()
        hit = None
        for it in items:
            author = (it.get("author") or "").strip()
            if author == name:
                hit = it
                break
        if not hit:
            for it in items:
                author = (it.get("author") or "").strip()
                if author and (name in author or author in name):
                    hit = it
                    break
        if not hit:
            return fields
        debut = hit.get("debut_year")
        if isinstance(debut, int) and debut > 1950:
            fields["debut_date"] = f"{debut}年"
            from datetime import date
            years = date.today().year - debut
            if years > 0:
                fields["active_years"] = f"{years} 年"
        desc = (hit.get("description") or "").strip()
        if desc:
            fields["bio"] = desc
        cat = (hit.get("category") or "").strip()
        if cat:
            fields["nationality"] = cat  # 如 "日本女优"
        # 头像：JSON image 字段（相对路径拼 base）
        img = (hit.get("image") or "").strip()
        if img:
            if img.startswith("/"):
                fields["avatar_url"] = "https://laoshi.ink" + img
            elif img.startswith("assets/"):
                fields["avatar_url"] = "https://laoshi.ink/" + img
        return fields
    except Exception as e:
        logger.info(f"laoshi 抓取失败 {name}: {e}")
        return fields


# ── 主入口：minnano + laoshi 双源整合 ──
def fetch_profile(name: str, name_en: Optional[str] = None) -> dict:
    """按用户指定逻辑聚合双源：minnano-av（个人信息）+ laoshi.ink（百科维度）。

    返回 {ok, source, fields}——source: minnano/laoshi/unknown/none
    每个源都优先用中文名、失败再用英文名试一遍；结果写入 INFO 日志（前端应用日志可见）。
    """
    # ① minnano-av（个人信息）
    fields = fetch_minnano(name)
    if not fields and name_en:
        fields = fetch_minnano(name_en)
    source = "minnano" if fields else None

    # ② laoshi.ink（百科维度，合并进 fields）
    laoshi = fetch_laoshi(name)
    if not laoshi and name_en:
        laoshi = fetch_laoshi(name_en)
    if laoshi:
        for k, v in laoshi.items():
            if k == "avatar_url" and v:
                # laoshi 头像（61KB 高清）质量高于 minnano 125px 小图，覆盖
                fields["avatar_url"] = v
            elif k not in fields:
                fields[k] = v
        source = source or "laoshi"

    if fields:
        logger.info("演员资料聚合 %s: 命中 %s fields=%s", name, source or "unknown", ",".join(sorted(fields)))
        return {"ok": True, "source": source or "unknown", "fields": fields}
    logger.info("演员资料聚合 %s: 双源均未命中（minnano/laoshi 均无结果）", name)
    return {"ok": False, "source": None, "fields": {}, "message": "minnano 与老师图鉴均未查询到该演员资料"}
