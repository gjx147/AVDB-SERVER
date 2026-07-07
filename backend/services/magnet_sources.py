"""磁力多源搜索适配器 —— 5 源并发 + 去重 + 画质识别。

参考 JavdBviewed magnetSearchManager，服务端化：
- sukebei / btdig / btsow / torrentz2 / javbus 五源
- asyncio.gather 并发 + 容错扇出（单源失败不影响）
- 结果按番号去重，识别画质后缀（-UC/-C/-U）

注意：反爬严的源（javbus/sukebei）可能需要浏览器池兜底。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("avdb.magnet_sources")

_CODE_RE = re.compile(r"([A-Za-z]{2,6})[-_]?(\d{2,5})")
_QUALITY_SUFFIXES = ["-UC", "-C", "-U"]


@dataclass
class MagnetResult:
    magnet: str
    name: str
    source: str
    size: str | None = None
    date: str | None = None
    quality: str | None = None  # UC/C/U/None


def _detect_quality(name: str) -> str | None:
    upper = name.upper()
    for q in _QUALITY_SUFFIXES:
        if q in upper:
            return q.lstrip("-")
    return None


def _extract_hash(magnet: str) -> str | None:
    m = re.search(r"btih:([a-fA-F0-9]{40})", magnet)
    return m.group(1).lower() if m else None


async def _search_sukebei(code: str) -> list[MagnetResult]:
    """sukebei.nyaa.si 搜索。"""
    url = f"https://sukebei.nyaa.si/?f=0&c=0_0&q={code}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            for tr in soup.select("table.torrent-list tr"):
                links = tr.select("a[href^='magnet:']")
                if not links:
                    continue
                magnet = links[0].get("href", "")
                name = links[0].get("title", "") or links[0].get_text(strip=True)
                tds = tr.find_all("td")
                size = tds[1].get_text(strip=True) if len(tds) > 1 else None
                date = tds[0].get_text(strip=True) if tds else None
                results.append(MagnetResult(magnet=magnet, name=name, source="sukebei", size=size, date=date, quality=_detect_quality(name)))
            return results
    except Exception as e:
        logger.debug(f"sukebei 搜索失败: {e}")
        return []


async def _search_btdig(code: str) -> list[MagnetResult]:
    """btdig.com 搜索。"""
    url = f"https://btdig.com/search?q={code}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            for div in soup.select("div.one_result"):
                a = div.select_one("a.torrent_name")
                if not a:
                    continue
                href = a.get("href", "")
                # btdig 的链接格式：/xxxxx/hash
                if href.startswith("http"):
                    import urllib.parse
                    # 构造 magnet
                    m = re.search(r"/([a-f0-9]{40})", href)
                    if m:
                        h = m.group(1)
                        magnet = f"magnet:?xt=urn:btih:{h}"
                        name = a.get_text(strip=True)
                        results.append(MagnetResult(magnet=magnet, name=name, source="btdig", quality=_detect_quality(name)))
            return results
    except Exception as e:
        logger.debug(f"btdig 搜索失败: {e}")
        return []


async def _search_btsow(code: str) -> list[MagnetResult]:
    """btsow.com 搜索。"""
    url = f"https://btsow.motorcycles/search/{code}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            for a in soup.select("a.row"):
                href = a.get("href", "")
                m = re.search(r"/([A-Fa-f0-9]{40})", href)
                if m:
                    h = m.group(1).lower()
                    magnet = f"magnet:?xt=urn:btih:{h}"
                    name = a.select_one(".file") or a
                    name = name.get_text(strip=True)
                    results.append(MagnetResult(magnet=magnet, name=name, source="btsow", quality=_detect_quality(name)))
            return results
    except Exception as e:
        logger.debug(f"btsow 搜索失败: {e}")
        return []


# torrentz2 / javbus 需要更复杂的反爬处理，先留占位
async def _search_torrentz2(code: str) -> list[MagnetResult]:
    return []  # TODO: torrentz2 反爬待处理


async def _search_javbus(code: str) -> list[MagnetResult]:
    """javbus 需要通过浏览器池抓取（反爬严），此处占位。"""
    return []  # TODO: 接入浏览器池


_SOURCES = {
    "sukebei": _search_sukebei,
    "btdig": _search_btdig,
    "btsow": _search_btsow,
    "torrentz2": _search_torrentz2,
    "javbus": _search_javbus,
}


async def search_all(code: str, sources: list[str] | None = None) -> dict:
    """多源并发搜索 + 去重 + 排序。返回按源分组的结果。"""
    active_sources = sources or list(_SOURCES.keys())
    coros = [_SOURCES[s](code) for s in active_sources if s in _SOURCES]
    results = await asyncio.gather(*coros, return_exceptions=True)

    # 合并去重（按 info_hash）
    seen_hashes: dict[str, MagnetResult] = {}
    by_source: dict[str, list[dict]] = {}
    total = 0
    for i, result in enumerate(results):
        source_name = active_sources[i] if i < len(active_sources) else f"src{i}"
        if isinstance(result, Exception):
            by_source[source_name] = []
            continue
        items = []
        for r in result:
            h = _extract_hash(r.magnet)
            if h and h in seen_hashes:
                continue
            if h:
                seen_hashes[h] = r
            items.append({"magnet": r.magnet, "name": r.name, "source": r.source,
                          "size": r.size, "date": r.date, "quality": r.quality})
            total += 1
        by_source[source_name] = items

    # 按画质排序（UC > C > U > 无）
    all_items = []
    for items in by_source.values():
        all_items.extend(items)
    all_items.sort(key=lambda x: (
        {"UC": 0, "C": 1, "U": 2}.get(x.get("quality") or "", 3)
    ))

    return {"total": len(all_items), "by_source": by_source, "items": all_items}
