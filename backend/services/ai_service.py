"""AI 服务层 —— OpenAI 兼容 ChatCompletion（翻译/标签/摘要/交互）。

参考 JavdBviewed aiService，服务端化：
- 配置存 settings 表（ai_base_url/ai_api_key/ai_model）
- 缓存存 llm_cache 表（prompt_hash -> response，省钱）
- 重试（空响应/可重试错误，指数退避）
- 三种任务：translate（标题翻译）/tags（标签生成）/summary（摘要）

统一走 OpenAI 兼容协议，支持任意兼容后端（OpenAI/DeepSeek/中转站等）。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from openai import AsyncOpenAI

logger = logging.getLogger("avdb.ai")

# 任务提示词模板
_PROMPTS = {
    "translate": "将以下日文影片标题翻译成中文，只输出译文，不要解释：\n{text}",
    "tags": "根据以下影片信息生成3-8个中文标签，用逗号分隔，只输出标签：\n标题：{text}",
    "summary": "用一两句话概括以下影片内容：\n{text}",
}


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

    base_url = config.get("ai_base_url", "").strip() or "https://api.openai.com/v1"
    api_key = config.get("ai_api_key", "").strip()
    use_model = model or config.get("ai_model", "").strip() or "gpt-3.5-turbo"
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
    # 重试：空响应 + 可重试错误
    for attempt in range(3):
        try:
            resp = await client.chat.completions.create(
                model=use_model, messages=messages, temperature=temperature, max_tokens=1000,
            )
            content = resp.choices[0].message.content or ""
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
