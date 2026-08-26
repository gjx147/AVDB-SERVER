"""标签翻译与中英检索兼容（标签英文问题修复）。

- 静态高频映射表（离线可用）
- LLM 动态映射（翻译库内实际标签，缓存）
- tag_translate_apply 更新 tasks.tags 为中文
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter

from sqlalchemy import select

logger = logging.getLogger("avdb.tag_translate")

# 静态中英映射（高频标签；中文 → 英文别名）
STATIC_TAG_MAP: dict[str, list[str]] = {
    "巨乳": ["Big Tits", "Huge Tits", "Big Boobs"],
    "熟女": ["Mature", "Milf"],
    "无码": ["Uncensored", "No Mosaic"],
    "中出": ["Creampie", "Nakadashi"],
    "人妻": ["Married Woman", "Housewife"],
    "OL": ["Office Lady", "Business Suit"],
    "制服": ["Uniform", "School Uniform"],
    "萝莉": ["Loli", "Petite"],
    "凌辱": ["Humiliation", "Rape", "Non-consent"],
    "足交": ["Footjob"],
    "口交": ["Blowjob", "Fellatio"],
    "颜射": ["Facial"],
    "肛交": ["Anal"],
    "素人": ["Amateur"],
    "单体": ["Solo"],
    "共演": ["Multi", "Co-star"],
    "合集": ["Compilation", "Best"],
    "护士": ["Nurse"],
    "女教师": ["Female Teacher"],
    "教师": ["Teacher"],
    "眼镜": ["Glasses", "Megane"],
    "黑发": ["Black Hair"],
    "短发": ["Short Hair"],
    "金发": ["Blonde"],
    "长发": ["Long Hair"],
    "双马尾": ["Twintails"],
    "丰满": ["Chubby", "BBW"],
    "苗条": ["Slim", "Slender"],
    "天然": ["Natural"],
    "人工": ["Artificial", "Implants"],
    "露出": ["Exposure", "Public"],
    "Cosplay": ["Cosplay", "Costume"],
    "按摩": ["Massage"],
    "4K": ["4K"],
    "VR": ["VR", "Virtual Reality"],
    "中文字幕": ["Chinese Subtitle"],
    "无修正": ["Uncensored"],
    "剧情": ["Story", "Plot"],
    "纯爱": ["Romance", "Love Story"],
    "NTR": ["NTR", "Netorare"],
    "调教": ["Training", "Discipline"],
    "洗脑": ["Brainwash"],
    "催眠": ["Hypnosis"],
    "电击": ["Electric", "Vibrator"],
    "玩具": ["Toy", "Vibrator"],
    "自慰": ["Masturbation"],
    "潮吹": ["Squirting"],
    "美少女": ["Beautiful Girl", "Young"],
    "姐姐": ["Older Sister", "Onee"],
    "妹妹": ["Younger Sister", "Imouto"],
    "女仆": ["Maid"],
    "警察": ["Police"],
    "空姐": ["Stewardess"],
    "泳装": ["Swimsuit", "Bikini"],
    "内衣": ["Lingerie"],
    "丝袜": ["Stockings", "Pantyhose"],
    "高跟鞋": ["High Heels"],
    "捆绑": ["Bondage", "Rope"],
    "羞耻": ["Shame", "Embarrassment"],
    "露出癖": ["Exhibitionist"],
    "面接": ["Interview"],
    "办公": ["Office"],
    "出张": ["Business Trip"],
    "温泉": ["Hot Spring", "Onsen"],
    "旅行": ["Travel"],
    "电车": ["Train"],
    "厕所": ["Toilet"],
    "公园": ["Park"],
}


def cn_aliases(tag_cn: str) -> list[str]:
    """中文标签 → 匹配别名列表（含原文）。"""
    aliases = [tag_cn]
    for cn, ens in STATIC_TAG_MAP.items():
        if cn == tag_cn or tag_cn in cn:
            aliases.extend(ens)
        for en in ens:
            if en.lower() == tag_cn.lower():
                aliases.append(cn)
    return list(dict.fromkeys(aliases))  # 去重保序


def _all_tags(db) -> Counter:
    from models import Task
    c: Counter = Counter()
    for (tags,) in db.execute(select(Task.tags).where(Task.tags.isnot(None))).all():
        for t in (tags or "").split(","):
            t = t.strip()
            if t:
                c[t] += 1
    return c


async def tag_translate_preview(min_count: int = 2, limit: int = 60) -> dict:
    """统计高频英文标签 → LLM 生成中英映射（预览，不执行）。"""
    from database import SessionLocal
    db = SessionLocal()
    try:
        c = _all_tags(db)
    finally:
        db.close()
    if not c:
        return {"ok": True, "mapping": {}, "total_tags": 0, "message": "库内暂无标签"}

    # 优先取可能为英文的标签（含 ASCII 字母）
    candidates = [t for t, n in c.most_common(400) if n >= min_count and re.search(r"[A-Za-z]", t)][:limit]
    if len(candidates) < 3:
        return {"ok": True, "mapping": {}, "total_tags": len(c), "message": "标签多为中文，无需翻译"}

    prompt = (
        f"以下是影片库中的英文标签：{'、'.join(candidates)}。\n"
        "请翻译成中文标签，输出 JSON 对象：{\"Big Tits\": \"巨乳\", ...}（值必须是常用中文标签词，不要翻译成人名）。\n"
        "只输出 JSON。"
    )
    mapping: dict[str, str] = {}
    try:
        from services.ai_service import chat
        raw = await chat(
            [{"role": "system", "content": "你是标签翻译助手，只输出 JSON。"},
             {"role": "user", "content": prompt}],
            task_type="tag_translate",
        )
        m = re.search(r"\{[\s\S]*\}", raw or "")
        if m:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, dict):
                mapping = {str(k).strip(): str(v).strip() for k, v in parsed.items()
                           if str(k).strip() and str(v).strip() and len(str(v).strip()) <= 12}
    except Exception as e:
        logger.warning("标签翻译 AI 失败: %s", e)
    if not mapping:
        return {"ok": True, "mapping": {}, "total_tags": len(c),
                "message": "未生成映射（可尝试手动在设置页处理，或确认 AI 已配置）"}
    return {"ok": True, "mapping": mapping, "total_tags": len(c), "count": len(mapping)}


def tag_translate_apply(mapping: dict[str, str]) -> dict:
    """应用映射：tasks.tags 中的英文标签替换为中文（保序去重）。"""
    from models import Task
    from database import SessionLocal
    if not mapping:
        return {"ok": False, "message": "映射为空"}
    db = SessionLocal()
    updated = 0
    try:
        rows = db.execute(select(Task).where(Task.tags.isnot(None))).scalars().all()
        for t in rows:
            parts = [p.strip() for p in (t.tags or "").split(",") if p.strip()]
            changed = False
            out = []
            for p in parts:
                np = mapping.get(p, p)
                if np != p:
                    changed = True
                if np not in out:
                    out.append(np)
            if changed:
                t.tags = ",".join(out)
                updated += 1
        db.commit()
    finally:
        db.close()
    return {"ok": True, "updated_tasks": updated, "mapping_count": len(mapping)}
