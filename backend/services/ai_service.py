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
    from sqlalchemy import select

    from database import SessionLocal
    from models import LLMCache
    db = SessionLocal()
    try:
        # LLMCache 主键是 id，prompt_hash 需按列查询（原 get(pk) 死代码与 __import__ 反模式已删除）
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
    """Generic ChatCompletion call (cache + retry + concurrency limit)."""
    config = await _get_config()
    if config.get("ai_enabled", "").lower() != "true":
        return ""

    base_url = config.get("ai_base_url", "").strip() or DEFAULT_BASE_URL
    api_key = config.get("ai_api_key", "").strip()
    use_model = model or config.get("ai_model", "").strip() or DEFAULT_MODEL
    if not api_key:
        logger.warning("AI key not configured")
        return ""

    if not _breaker_allows():
        logger.warning("AI breaker open, skip LLM call (fallback)")
        return ""

    prompt_text = str(messages)
    prompt_hash = _hash_prompt(prompt_text)
    if use_cache:
        cached = _get_cached(prompt_hash)
        if cached:
            logger.debug("AI cache hit: %s", prompt_hash[:12])
            return cached

    # C5: per-hash lock prevents double LLM billing on concurrent same request
    lock = await _acquire_chat_lock(prompt_hash)
    async with lock:
        if use_cache:
            cached = _get_cached(prompt_hash)
            if cached:
                return cached
        return await _chat_uncached(base_url, api_key, use_model, messages, temperature,
                                    use_cache, prompt_hash, task_type, prompt_text)


# E1: pooled client (per base_url+api_key), avoids connection leak
import threading
_client_pool: dict[tuple[str, str], object] = {}
_client_pool_guard = threading.Lock()

# C5: per-hash in-process locks, prevent double billing
_chat_locks: dict[str, asyncio.Lock] = {}
_chat_locks_guard = threading.Lock()

# E6: global LLM concurrency semaphore
_llm_semaphore = asyncio.Semaphore(4)

# Circuit breaker: 5 consecutive failures -> open 5min -> half-open probe
_breaker = {"state": "closed", "failures": 0, "opened_at": None}
_BREAKER_THRESHOLD = 5
_BREAKER_OPEN_SECONDS = 300


def breaker_status() -> dict:
    """当前熔断器状态。"""
    st = dict(_breaker)
    if st["state"] == "open":
        remain = _BREAKER_OPEN_SECONDS - (time.monotonic() - (st["opened_at"] or 0))
        st["remaining_seconds"] = max(0, int(remain))
    return st


def _breaker_allows() -> bool:
    st = _breaker
    if st["state"] == "closed":
        return True
    if st["state"] == "open":
        if time.monotonic() - (st["opened_at"] or 0) > _BREAKER_OPEN_SECONDS:
            st["state"] = "half-open"
            return True
        return False
    # half-open: allow single probe
    return True


def _breaker_record(success: bool) -> None:
    st = _breaker
    if success:
        st["state"] = "closed"
        st["failures"] = 0
        st["opened_at"] = None
    else:
        st["failures"] += 1
        if st["failures"] >= _BREAKER_THRESHOLD:
            st["state"] = "open"
            st["opened_at"] = time.monotonic()


def _get_client(base_url: str, api_key: str):
    """Lazy singleton client; rebuilds when config changes."""
    key = (base_url, api_key)
    c = _client_pool.get(key)
    if c is not None:
        return c
    with _client_pool_guard:
        c = _client_pool.get(key)
        if c is None:
            c = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=60)
            _client_pool[key] = c
    return c


_CHAT_LOCKS_MAX = 500


async def _acquire_chat_lock(prompt_hash: str) -> asyncio.Lock:
    with _chat_locks_guard:
        lock = _chat_locks.get(prompt_hash)
        if lock is None:
            if len(_chat_locks) > _CHAT_LOCKS_MAX:
                _chat_locks.clear()  # 防内存膨胀（锁重建代价可忽略）
            lock = asyncio.Lock()
            _chat_locks[prompt_hash] = lock
        return lock


def _save_usage(task_type: str, model: str, prompt_tokens: int, completion_tokens: int,
                 duration_ms: int, cache_hit: bool = False, ok: bool = True) -> None:
    """Record AI usage (independent short session)."""
    try:
        from database import SessionLocal
        from models import AiUsage
        db = SessionLocal()
        try:
            db.add(AiUsage(task_type=task_type[:50], model=(model or "")[:100],
                           prompt_tokens=int(prompt_tokens or 0), completion_tokens=int(completion_tokens or 0),
                           duration_ms=int(duration_ms or 0), cache_hit=bool(cache_hit), ok=bool(ok)))
            db.commit()
        finally:
            db.close()
    except Exception:
        pass


async def _chat_uncached(base_url, api_key, use_model, messages, temperature,
                         use_cache, prompt_hash, task_type, prompt_text) -> str:
    """Uncached path: pooled client + classified retry (E1/E2)."""
    import openai
    client = _get_client(base_url, api_key)
    extra_body = {}
    if use_model.startswith("MiniMax-M3"):
        extra_body["thinking"] = {"type": "disabled"}

    _t0 = time.monotonic()
    _last_prompt = 0
    _last_completion = 0

    async def _call_with_retry() -> str:
        nonlocal _last_prompt, _last_completion
        for attempt in range(2):
            try:
                resp = await client.chat.completions.create(
                    model=use_model, messages=messages, temperature=temperature, max_tokens=1000,
                    extra_body=extra_body or None,
                )
                content = _strip_thinking(resp.choices[0].message.content or "")
                usage = getattr(resp, "usage", None)
                if usage is not None:
                    _last_prompt = getattr(usage, "prompt_tokens", 0) or 0
                    _last_completion = getattr(usage, "completion_tokens", 0) or 0
                if content.strip():
                    if use_cache:
                        _save_cache(prompt_hash, task_type, use_model, prompt_text, content)
                    return content.strip()
                logger.warning("AI empty response (attempt %d)", attempt + 1)
            except openai.APIStatusError as e:
                sc = e.status_code
                if sc < 500 and sc != 429:
                    logger.warning("AI non-retryable error (%s)", sc)
                    return ""
                logger.warning("AI retryable error (%s, attempt %d)", sc, attempt + 1)
            except Exception as e:
                logger.warning("AI call failed (attempt %d): %s", attempt + 1, e)
            if attempt < 1:
                await asyncio.sleep(1)
        _breaker_record(False)
        _save_usage(task_type, use_model, 0, 0, int((time.monotonic() - _t0) * 1000), ok=False)
        return ""

    try:
        async with _llm_semaphore:
            text = await asyncio.wait_for(_call_with_retry(), timeout=90)
        if text:
            _breaker_record(True)
        _save_usage(task_type, use_model, _last_prompt, _last_completion,
                    int((time.monotonic() - _t0) * 1000), ok=bool(text))
        return text
    except asyncio.TimeoutError:
        logger.error("AI overall timeout (>90s): task=%s model=%s", task_type, use_model)
        _breaker_record(False)
        _save_usage(task_type, use_model, 0, 0, int((time.monotonic() - _t0) * 1000), ok=False)
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
