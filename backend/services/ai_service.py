"""AI 服务层 —— OpenAI 兼容 ChatCompletion（翻译/标签/摘要/交互）。

参考 JavdBviewed aiService，服务端化：
- 配置存 settings 表（ai_base_url/ai_api_key/ai_model）
- 缓存存 llm_cache 表（prompt_hash -> response，省钱）
- 重试（空响应/可重试错误，指数退避）
- 三种任务：translate（标题翻译）/tags（标签生成）/summary（摘要）

统一走 OpenAI 兼容协议。默认指向 MiniMax（platform.minimaxi.com）：
- base_url https://api.minimaxi.com/v1，Bearer API Key
- MiniMax-M2.x 思考链不可关闭、会以 <think>…</think> 嵌在 content 里 → 统一剥离
- MiniMax-M3 可显式关闭 thinking（extra_body），更快更省
也兼容其他 OpenAI 兼容后端（OpenAI/DeepSeek/中转站等）。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from typing import Any

from openai import AsyncOpenAI

logger = logging.getLogger("avdb.ai")

# MiniMax 默认配置（settings 表未配置时的兜底）
DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
DEFAULT_MODEL = "MiniMax-M2.7"

# 任务提示词模板
_PROMPTS = {
    "translate": "将以下日文影片标题翻译成中文，只输出译文，不要解释：\n{text}",
    "tags": "根据以下影片信息生成3-8个中文标签，用逗号分隔，只输出标签：\n标题：{text}",
    "summary": "用一两句话概括以下影片内容：\n{text}",
}

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_thinking(content: str) -> str:
    """剥离 MiniMax M2.x 思考链标签（reasoning 默认内嵌 content）。"""
    return _THINK_RE.sub("", content).strip()


async def _get_config() -> dict[str, str]:
    from database import SessionLocal
    from models import Setting
    keys = ["ai_base_url", "ai_api_key", "ai_model", "ai_enabled"]
    db = SessionLocal()
    try:
        return {k: (db.get(Setting, k).value if db.get(Setting, k) else "") for k in keys}
    finally:
        db.close()


def _hash_prompt(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _get_cached(prompt_hash: str) -> str | None:
    from database import SessionLocal
    from models import LLMCache
    db = SessionLocal()
    try:
        row = db.get(LLMCache, prompt_hash) or db.execute(
            __import__("sqlalchemy").select(LLMCache).where(LLMCache.prompt_hash == prompt_hash)
        ).scalar_one_or_none()
        # get by pk 不行（pk 是 id），用 prompt_hash 查
        from sqlalchemy import select
        row = db.execute(select(LLMCache).where(LLMCache.prompt_hash == prompt_hash)).scalar_one_or_none()
        return row.response if row else None
    finally:
        db.close()


def _save_cache(prompt_hash: str, task_type: str, model: str, prompt: str, response: str) -> None:
    from database import SessionLocal
    from models import LLMCache
    from sqlalchemy import select
    db = SessionLocal()
    try:
        existing = db.execute(select(LLMCache).where(LLMCache.prompt_hash == prompt_hash)).scalar_one_or_none()
        if existing:
            existing.response = response
        else:
            db.add(LLMCache(prompt_hash=prompt_hash, task_type=task_type, model=model, prompt=prompt, response=response))
        db.commit()
    finally:
        db.close()


async def chat(messages: list[dict], *, task_type: str = "chat", model: str | None = None,
               temperature: float = 0.3, use_cache: bool = True) -> str:
    """通用 ChatCompletion 调用（带缓存+重试）。"""
    config = await _get_config()
    if config.get("ai_enabled", "").lower() != "true":
        return ""

    base_url = config.get("ai_base_url", "").strip() or DEFAULT_BASE_URL
    api_key = config.get("ai_api_key", "").strip()
    use_model = model or config.get("ai_model", "").strip() or DEFAULT_MODEL
    if not api_key:
        logger.warning("AI 未配置 api_key")
        return ""

    # 缓存检查
    prompt_text = str(messages)
    prompt_hash = _hash_prompt(prompt_text)
    if use_cache:
        cached = _get_cached(prompt_hash)
        if cached:
            logger.debug("AI 命中缓存: %s", prompt_hash[:12])
            return cached

    client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=60)
    # MiniMax-M3 可显式关思考（快+省）；M2.x 不支持该参数，靠下游 _strip_thinking
    extra_body = {}
    if use_model.startswith("MiniMax-M3"):
        extra_body["thinking"] = {"type": "disabled"}
    # 重试：空响应 + 可重试错误
    for attempt in range(3):
        try:
            resp = await client.chat.completions.create(
                model=use_model, messages=messages, temperature=temperature, max_tokens=1000,
                extra_body=extra_body or None,
            )
            content = _strip_thinking(resp.choices[0].message.content or "")
            if content.strip():
                if use_cache:
                    _save_cache(prompt_hash, task_type, use_model, prompt_text, content)
                return content.strip()
            logger.warning("AI 空响应(attempt %d)", attempt + 1)
        except Exception as e:
            logger.warning("AI 调用失败(attempt %d): %s", attempt + 1, e)
        if attempt < 2:
            wait = 2 ** attempt  # 1s, 2s 指数退避
            await asyncio.sleep(wait)
    return ""


async def test_connection() -> dict:
    """用当前 DB 配置发一句问候，验证 AI 连通性（不写缓存）。"""
    t0 = time.monotonic()
    config = await _get_config()
    if config.get("ai_enabled", "").lower() != "true":
        return {"ok": False, "message": "AI 未启用（ai_enabled 不是 true）"}
    api_key = config.get("ai_api_key", "").strip()
    if not api_key:
        return {"ok": False, "message": "未配置 API Key"}
    base_url = config.get("ai_base_url", "").strip() or DEFAULT_BASE_URL
    use_model = config.get("ai_model", "").strip() or DEFAULT_MODEL
    reply = await chat(
        [{"role": "user", "content": "只回复两个字：你好"}],
        task_type="test", temperature=0.1, use_cache=False,
    )
    elapsed = time.monotonic() - t0
    if reply:
        return {"ok": True, "message": f"连通正常（{use_model}，{elapsed:.1f}s）：{reply[:40]}"}
    return {"ok": False, "message": f"调用失败（{base_url} / {use_model}），请检查 Key 与模型名"}


async def translate(text: str, *, model: str | None = None) -> str:
    """翻译标题（日文→中文）。"""
    prompt = _PROMPTS["translate"].format(text=text)
    return await chat([{"role": "user", "content": prompt}], task_type="translate", model=model)


async def generate_tags(text: str, *, model: str | None = None) -> list[str]:
    """生成标签。"""
    prompt = _PROMPTS["tags"].format(text=text)
    result = await chat([{"role": "user", "content": prompt}], task_type="tags", model=model)
    return [t.strip() for t in result.split(",") if t.strip()]


async def summarize(text: str, *, model: str | None = None) -> str:
    """生成摘要。"""
    prompt = _PROMPTS["summary"].format(text=text)
    return await chat([{"role": "user", "content": prompt}], task_type="summary", model=model)


async def enrich_task(task_id: int) -> dict:
    """对单个任务执行 AI 增强（翻译标题 + 生成标签）。"""
    from database import SessionLocal
    from models import Task
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if not task or not task.title:
            return {"ok": False, "message": "任务不存在或无标题"}
        translated = await translate(task.title)
        tags = await generate_tags(task.title)
        changed = False
        if translated and translated != task.title:
            task.ai_title_translated = translated
            changed = True
        if tags:
            task.ai_tags = ",".join(tags)
            changed = True
        if changed:
            db.commit()
        return {"ok": True, "task_id": task_id, "translated": translated, "tags": tags, "changed": changed}
    finally:
        db.close()


# ── V3 AI 耳语：按影片元数据生成一句挑逗推荐语（影库女主人人格）──
_WHISPER_SYSTEM = (
    "你是一座私人影片影库的女主人，正陪着主人浏览他的收藏。"
    "你的声音低而近，像贴着耳廓说话：短句、留白、多用第二人称。"
    "你知情识趣，永远比对方半步从容；说话时把欲望写得像诗——"
    "用温度、重量、距离、衣料的暗喻，绝不直呼身体器官，不使用任何粗俗词汇。"
    "任务：根据给定的影片信息，写一句不超过 24 个中文字的挑逗推荐语，"
    "只输出这一句话本身，不要引号、不要解释、不要出现番号和片商名。"
)

_TONE_INSTRUCTIONS = {
    0: "语气克制含蓄，像随口一提的邀请。",
    1: "语气大胆亲昵，像靠近半步的低语。",
    2: "语气直接滚烫、性张力拉满，但仍保持文学性暗喻，不落俗。",
}


async def whisper_line(task_id: int, tone: int = 0, night: bool = False) -> str:
    """为 Wall 轮播/今夜情人生成一句耳语情话（llm_cache 自动缓存）。"""
    from database import SessionLocal
    from models import Task

    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if not task:
            return ""
        meta = "；".join(filter(None, [
            f"标题：{task.title or ''}",
            f"演员：{task.actors or ''}",
            f"标签：{task.tags or ''}",
            f"评分：{task.rating or ''}",
        ]))
    finally:
        db.close()

    tone_key = 0 if tone <= 0 else (2 if tone >= 2 else 1)
    user = (
        f"影片信息 —— {meta}\n"
        f"{_TONE_INSTRUCTIONS[tone_key]}"
        + ("此刻是深夜，语气更沉、更近。" if night else "")
    )
    line = await chat(
        [{"role": "system", "content": _WHISPER_SYSTEM},
         {"role": "user", "content": user}],
        task_type="whisper", temperature=0.9,
    )
    # 截断到 40 字内（防模型超写）
    return line.strip().strip('“”"')[:40]
