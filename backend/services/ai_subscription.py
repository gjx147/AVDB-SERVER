"""AI 订阅服务（S1/S3）——自然语言 → composite filters_json → 预览/创建。

- S1: NL 一句话生成过滤条件（genres/makers/series/min_rating/date_from/exclude_codes）
- S3: 从作品提取特征生成过滤条件（同演员/系列/厂牌后续追更）
- 降级: 未配置 AI 时用关键词规则提取（评分/标签/番号前缀）
"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy import select
from models import Task

logger = logging.getLogger("avdb.ai_subscription")

FILTER_SCHEMA = (
    '{"makers": ["厂商白名单"], "labels": ["厂牌白名单"], "series": ["系列白名单"], '
    '"genres": ["标签白名单，命中任一即可"], "exclude_codes": ["番号前缀黑名单，如 VR-"], '
    '"min_rating": 最低评分数字或 null, "date_from": "YYYY-MM-DD 或 null"}'
)


async def parse_filters_from_text(text: str) -> tuple[dict, str]:
    """NL → (filters_json_dict, engine)。LLM 优先，规则降级。"""
    from services.ai_service import _get_cached, _hash_prompt, _save_cache, chat

    text = (text or "").strip()
    if not text:
        return {}, "rules"

    key = _hash_prompt(f"sub_filters:{text}")
    cached = _get_cached(key)
    if cached:
        try:
            return json.loads(cached), "cache"
        except Exception:
            pass

    schema = FILTER_SCHEMA
    prompt = (
        "你是影片库订阅助手。把用户一句话订阅条件转换成 composite 订阅过滤 JSON，只输出 JSON，不要其它文字。\n"
        f"可用字段（全部可选，未知填 null 或空数组）：{schema}\n"
        f"用户条件：{text}\n"
        "注意：genres 用常见标签词（巨乳/熟女/中出/无码等）；min_rating 按 0-10 分制；"
        "exclude_codes 用于排除（如 不要VR 则填 [\"VR-\"]）。"
    )
    try:
        raw = await chat(
            [{"role": "system", "content": "你是影片库订阅助手，只输出 JSON。"},
             {"role": "user", "content": prompt}],
            task_type="sub_filters",
        )
        m = re.search(r"\{[\s\S]*\}", raw or "")
        if m:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, dict):
                _save_cache(key, "sub_filters", "", f"sub_filters:{text}", m.group(0))
                return parsed, "ai"
    except Exception:
        pass

    # 规则降级
    filters: dict = {}
    m = re.search(r"(\d+(?:\.\d+)?)\s*分", text)
    if m:
        filters["min_rating"] = float(m.group(1))
    for tag in ("无码", "中出", "巨乳", "熟女", "萝莉", "人妻", "OL", "制服", "凌辱", "足交"):
        if tag in text:
            filters.setdefault("genres", []).append(tag)
    m = re.search(r"不要([A-Z]{2,5})-", text.upper())
    if m:
        filters.setdefault("exclude_codes", []).append(f"{m.group(1)}-")
    return filters, "rules"


def count_matches(db, filters: dict) -> int:
    """统计当前库内命中数（预览用）。"""
    stmt = select(Task.id).where(Task.status == "visited")
    genres = filters.get("genres") or []
    if genres:
        from sqlalchemy import or_
        stmt = stmt.where(or_(*[Task.tags.like(f"%{g}%") for g in genres[:5]]))
    makers = filters.get("makers") or []
    if makers:
        from sqlalchemy import or_
        stmt = stmt.where(or_(*[Task.maker == mk for mk in makers[:5]]))
    labels = filters.get("labels") or []
    if labels:
        from sqlalchemy import or_
        stmt = stmt.where(or_(*[Task.label == lb for lb in labels[:5]]))
    series = filters.get("series") or []
    if series:
        from sqlalchemy import or_
        stmt = stmt.where(or_(*[Task.series == sr for sr in series[:5]]))
    ex = filters.get("exclude_codes") or []
    for p in ex:
        if p:
            stmt = stmt.where(Task.video_code.notlike(f"{p}%"))
    rm = filters.get("min_rating")
    if rm is not None:
        try:
            stmt = stmt.where(Task.rating >= float(rm))
        except Exception:
            pass
    df = filters.get("date_from")
    if df:
        stmt = stmt.where(Task.release_date >= str(df))
    return len(db.execute(stmt).scalars().all())


def default_name(text: str) -> str:
    """订阅默认名：取一句话前 12 字。"""
    t = (text or "").strip().replace("订阅", "").strip()
    return t[:12] or "AI 订阅"


def build_filters_from_task(task) -> dict:
    """S3: 从作品提取特征（演员/厂商/厂牌/系列/标签 → filters）。"""
    filters: dict = {}
    if task.maker:
        filters["makers"] = [task.maker]
    if task.label:
        filters["labels"] = [task.label]
    if task.series:
        filters["series"] = [task.series]
    tags = [t.strip() for t in (task.tags or "").split(",") if t.strip()][:5]
    if tags:
        filters["genres"] = tags
    if task.video_code:
        m = re.match(r"^[A-Z]{2,5}-", task.video_code.upper())
        if m:
            filters["exclude_codes"] = [m.group(0)]
    return filters
