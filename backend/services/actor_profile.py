"""女优资料三源聚合器 —— 中文维基（优先）→ minnano-av → laoshi.ink。

全自动定时任务与手动重试共用。全部走 httpx 纯请求（无浏览器/无 CF 对抗），
代理从 DB settings 的 http_proxy 读（与 browser_pool 同款）。

回退逻辑（用户指定）：
1. 中文维基：四维度一次拿齐（个人信息 + 简介 + 时间线 + 出道/活跃年限）
2. minnano-av：个人信息（生日/三围/罩杯/身高/血型/星座）
3. laoshi.ink：人物简介 / 职业时间线 / 出道年份 / 活跃年限
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger("avdb.actor_profile")

# 各源单请求超时
_TIMEOUT = 15.0

# 维基信息框参数 → 目标字段（模板名 |param= 值）
_WIKI_MAP = [
    ("birth_date", r"出生日期\s*=\s*\{\{birth date and age\|(\d{4})\|(\d{1,2})\|(\d{1,2})"),
    ("height", r"身長\s*=\s*([\d.]+)"),
    ("cup", r"カップ\s*=\s*([A-Z][A-Z]?)"),
    ("blood_type", r"血液型\s*=\s*\[\[ABO式血液型\|([^\]]+)\]\]"),
    ("birthplace", r"出身地\s*=\s*([^\n|]+)"),
    ("active_years", r"AV出演期間\s*=\s*([^\n|]+)"),
    ("alias", r"别名\s*=\s*([^\n<|]+)"),
    ("nationality", r"国籍\s*=\s*([^\n|]+)"),
]
# 三围：三个独立参数
_WIKI_BUST = re.compile(r"バスト\s*=\s*([\d.]+)")
_WIKI_WAIST = re.compile(r"ウエスト\s*=\s*([\d.]+)")
_WIKI_HIP = re.compile(r"ヒップ\s*=\s*([\d.]+)")


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


def _client() -> httpx.Client:
    proxy = _get_proxy()
    return httpx.Client(timeout=_TIMEOUT, follow_redirects=True,
                        proxy=proxy if proxy else None,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"})


def _clean_wiki(text: str) -> str:
    """清洗 wiki 标记：ref/cite 模板、[[链接]]、''加粗''、{{模板}}、<br>。"""
    t = re.sub(r"<ref[^>]*>.*?</ref>|<ref[^/]*/>", "", text, flags=re.S)
    t = re.sub(r"\{\{[Cc]ite[^}]*\}\}", "", t)
    t = re.sub(r"\{\{[^}]*\}\}", "", t)  # 剩余模板
    t = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", t)
    t = re.sub(r"''+", "", t)
    t = re.sub(r"<br\s*/?>", "\n", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# ── 源 1：中文维基 ──
def fetch_wikipedia(name: str) -> dict:
    """中文维基：搜索 → wikitext 四层解析（信息框/导语/时间线/活跃年限）。"""
    fields: dict = {}
    try:
        with _client() as c:
            r = c.get("https://zh.wikipedia.org/w/api.php", params={
                "action": "query", "list": "search", "srsearch": name,
                "format": "json", "srlimit": 3,
            })
            hits = r.json().get("query", {}).get("search", [])
        if not hits:
            return fields
        # 标题包含匹配校验（防同名不同人）
        title = hits[0]["title"]
        if name not in title and title not in name:
            return fields
        with _client() as c:
            r = c.get("https://zh.wikipedia.org/w/api.php", params={
                "action": "parse", "page": title, "prop": "wikitext",
                "format": "json", "section": 0,
            })
            wt = r.json().get("parse", {}).get("wikitext", {}).get("*", "")
        if not wt:
            return fields
        # ① 信息框参数
        for field, pattern in _WIKI_MAP:
            m = re.search(pattern, wt)
            if m:
                val = m.group(2) if field == "birth_date" else m.group(1)
                val = _clean_wiki(val).strip(" |=")
                if val:
                    fields[field] = val
        # 三围
        b, w, h = _WIKI_BUST.search(wt), _WIKI_WAIST.search(wt), _WIKI_HIP.search(wt)
        if b and w and h:
            fields["measurements"] = f"B{b.group(1)} / W{w.group(1)} / H{h.group(1)}"
        # ② 导语段（简介）：信息框结束后的第一个正文段落
        infobox_end = 0
        if "{{AV女優" in wt:
            depth = 0
            for i, ch in enumerate(wt):
                if wt.startswith("{{", i):
                    depth += 1
                elif wt.startswith("}}", i) and depth > 0:
                    depth -= 1
                    if depth == 0:
                        infobox_end = i + 2
                        break
        lead = _clean_wiki(wt[infobox_end:])
        # 首个非空段落（截到 300 字）
        for para in re.split(r"\n{2,}", lead):
            para = para.strip()
            if len(para) > 40 and not para.startswith("=="):
                fields["bio"] = para[:300]
                break
        # ③ 职业时间线：经历/経歴/生涯 章节下的年份行
        sec = re.search(r"==+[^=]*(经历|経歴|生涯|人物|経緯)[^=]*==+(.*?)(?:==+|\Z)", wt, re.S)
        timeline_lines: list[str] = []
        if sec:
            for line in sec.group(2).splitlines():
                line = _clean_wiki(line).strip()
                if re.match(r"^(19|20)\d{2}年", line) and len(line) > 6:
                    timeline_lines.append(line)
        if timeline_lines:
            fields["timeline"] = "\n".join(timeline_lines[:30])
        # ④ 出道年份/活跃年限：active_years 提取（如 "2015年 - 2023年"）
        if fields.get("active_years"):
            years = re.findall(r"(19|20)\d{2}", fields["active_years"])
            if years:
                start, end = int(years[0]), int(years[-1])
                fields["debut_date"] = f"{start}年"
                if end > start:
                    fields["active_years"] = f"{end - start} 年（{fields['active_years']}）"
        if fields.get("nationality") and ("日本" in fields["nationality"] or "Japan" in fields["nationality"]):
            fields["nationality"] = "日本"
        return fields
    except Exception as e:
        logger.debug(f"维基抓取失败 {name}: {e}")
        return fields


# ── 源 2：minnano-av ──
def fetch_minnano(name: str) -> dict:
    """minnano-av：搜索（search_word）→ 详情页资料行（生日/三围/罩杯/身高/血型/星座）。

    实测：精确匹配时搜索直接返回该演员 profile 页（title 含名字），
    否则为结果列表，取第一个 actressNNN.html 跟进。
    """
    fields: dict = {}
    try:
        with _client() as c:
            r = c.get("https://www.minnano-av.com/search_result.php", params={
                "search_scope": "actress", "search_word": name,
            })
            html = r.text
            # 列表页：提取第一个演员详情链接
            if name not in html:
                m = re.search(r"(actress\d+\.html)", html)
                if not m:
                    return fields
                r2 = c.get(f"https://www.minnano-av.com/{m.group(1)}")
                html = r2.text
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
        return fields
    except Exception as e:
        logger.debug(f"minnano 抓取失败 {name}: {e}")
        return fields


# ── 源 3：laoshi.ink ──
_LAOSHI_DATA: list[dict] | None = None  # 全量 JSON 缓存（600 演员，进程内）


def _laoshi_json() -> list[dict]:
    """下载 laoshi 全量数据（assets/data/light/jav.json，进程内缓存）。"""
    global _LAOSHI_DATA
    if _LAOSHI_DATA is not None:
        return _LAOSHI_DATA
    try:
        with _client() as c:
            r = c.get("https://laoshi.ink/assets/data/light/jav.json")
            data = r.json()
        _LAOSHI_DATA = data.get("rankingActors") or data.get("actors") or []
        logger.info(f"laoshi 数据已加载: {len(_LAOSHI_DATA)} 条")
    except Exception as e:
        logger.debug(f"laoshi 数据加载失败: {e}")
        _LAOSHI_DATA = []
    return _LAOSHI_DATA


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
        return fields
    except Exception as e:
        logger.debug(f"laoshi 抓取失败 {name}: {e}")
        return fields


# ── 主入口：三级回退 ──
def fetch_profile(name: str, name_en: Optional[str] = None) -> dict:
    """按用户指定逻辑聚合三源。

    返回 {ok, source, fields}——source: wikipedia/minnano/laoshi/none
    """
    # ① 中文维基（四维度一次拿齐）
    fields = fetch_wikipedia(name)
    if fields:
        return {"ok": True, "source": "wikipedia", "fields": fields}
    if name_en:
        fields = fetch_wikipedia(name_en)
        if fields:
            return {"ok": True, "source": "wikipedia", "fields": fields}

    # ② minnano-av（个人信息）
    fields = fetch_minnano(name)
    source = "minnano" if fields else None

    # ③ laoshi.ink（百科维度）
    laoshi = fetch_laoshi(name)
    if laoshi:
        fields.update({k: v for k, v in laoshi.items() if k not in fields})
        source = source or "laoshi"

    if fields:
        return {"ok": True, "source": source or "unknown", "fields": fields}
    return {"ok": False, "source": None, "fields": {}, "message": "三源均未查询到该演员资料"}
