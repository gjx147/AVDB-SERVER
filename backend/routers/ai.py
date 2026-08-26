"""AI 路由 —— 翻译/标签/摘要/任务增强/V3 耳语情话。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import Annotated
from pydantic import BaseModel, Field

from deps import CurrentUser, DbSession, CurrentAdmin, get_current_admin, get_current_user
from services.ai_service import enrich_task, generate_tags, summarize, translate, whisper_line, test_connection

router = APIRouter(prefix="/api/ai", tags=["ai"])


class TranslateRequest(BaseModel):
    text: str = Field(max_length=5000)
    model: str | None = Field(default=None, max_length=100)


class TagsRequest(BaseModel):
    text: str = Field(max_length=5000)
    model: str | None = Field(default=None, max_length=100)


class SummaryRequest(BaseModel):
    text: str = Field(max_length=5000)
    model: str | None = Field(default=None, max_length=100)


class WhisperRequest(BaseModel):
    task_id: int
    tone: int = Field(default=0, ge=0, le=2)  # 0 克制 / 1 大胆 / 2 露骨
    night: bool = False


@router.post("/translate")
async def ai_translate(req: TranslateRequest, _user: CurrentUser):
    result = await translate(req.text, model=req.model)
    return {"ok": bool(result), "translated": result}


@router.post("/tags")
async def ai_tags(req: TagsRequest, _user: CurrentUser):
    tags = await generate_tags(req.text, model=req.model)
    return {"ok": bool(tags), "tags": tags}


@router.post("/summary")
async def ai_summary(req: SummaryRequest, _user: CurrentUser):
    result = await summarize(req.text, model=req.model)
    return {"ok": bool(result), "summary": result}


@router.post("/enrich/{task_id}")
async def ai_enrich(task_id: int, _user: CurrentUser):
    """对任务执行 AI 增强（翻译标题+生成标签，写回 ai_title_translated/ai_tags）。"""
    result = await enrich_task(task_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "增强失败"))
    return result


@router.post("/whisper")
async def ai_whisper(req: WhisperRequest, _user: CurrentUser):
    """AI 耳语：按影片元数据生成一句挑逗推荐语（影库女主人人格，llm_cache 自动缓存）。
    AI 未配置/调用失败时 ok=false，前端静默回退静态文案池。"""
    line = await whisper_line(req.task_id, tone=req.tone, night=req.night)
    return {"ok": bool(line), "line": line}


@router.post("/test")
async def ai_test(_user: CurrentUser):
    """用当前 settings 表配置发一句问候，验证 AI 连通性（供设置页「测试连接」）。"""
    return await test_connection()



@router.post("/recommend-reason")
async def recommend_reason(payload: dict, db: DbSession, _user: CurrentUser):
    """为推荐作品生成一句话推荐理由（F8）。LLM 结果按 task_id 缓存，重复请求不花钱。"""
    from fastapi import HTTPException
    from models import Task

    task_id = payload.get("task_id")
    task = db.get(Task, task_id) if task_id else None
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    from services.ai_service import _get_cached, _hash_prompt, _save_cache, chat

    key = _hash_prompt(f"recommend:{task_id}")
    cached = _get_cached(key)
    if cached:
        return {"reason": cached, "cached": True}

    title = task.title or task.video_code or ""
    meta = f"番号 {task.video_code or '未知'}，评分 {task.rating or '暂无'}，标签：{task.tags or '无'}"
    try:
        reason = await chat(
            [
                {"role": "system", "content": "你是资深影评人，用一句话（40 字以内）向用户推荐一部作品，语气自然不浮夸，直接说推荐理由。"},
                {"role": "user", "content": f"作品：{title}。{meta}"},
            ],
            task_type="recommend",
        )
    except Exception as e:
        return {"reason": "", "cached": False, "error": str(e)[:200]}
    reason = (reason or "").strip()
    if reason:
        _save_cache(key, "recommend", "", f"recommend:{task_id}", reason)
    return {"reason": reason, "cached": False}


class AskRequest(BaseModel):
    question: str = Field(max_length=1000)
    # A1: 多轮对话上下文（[{role: user|assistant, content}], 保留最近 6 轮）
    history: list[dict] = Field(default_factory=list)


class AgentRequest(BaseModel):
    """A1+: 机器人助手对话（工具调用模式）。"""
    messages: list[dict] = Field(default_factory=list, description="对话消息 [{'role','content'}]，最后一条为用户请求")


@router.post("/agent")
async def agent_chat(req: AgentRequest, db: DbSession, _user: CurrentUser):
    """机器人助手：意图解析 → 工具调用 → 自然语言回复。写操作返回确认 token。"""
    from services.agent_service import agent_run
    messages = [m for m in (req.messages or []) if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    if not messages or messages[-1].get("role") != "user":
        raise HTTPException(status_code=400, detail="messages 最后一条必须为用户消息")
    return await agent_run(messages[-12:], db, _user)


def _agent_confirm_user(request: Request, db: DbSession) -> str:
    """AUTH_DISABLED（单用户本地模式）放行；正式模式要求管理员。"""
    from config import get_settings
    if get_settings().AUTH_DISABLED:
        return "anonymous"
    user = get_current_user(request, request.headers.get("authorization"))
    return get_current_admin(user, db)


@router.post("/agent/confirm")
def agent_confirm(req: ConfirmRequest, db: DbSession, _user: Annotated[str, Depends(_agent_confirm_user)]):
    """确认执行写工具（两段式：agent 返回 token 后用户确认）。"""
    from services.agent_service import agent_confirm as do_confirm
    result = do_confirm(req.token, db, _user)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "确认失败"))
    return result


class ConfirmRequest(BaseModel):
    token: str = Field(min_length=1, max_length=64)


@router.post("/ask")
async def ai_ask(req: AskRequest, db: DbSession, _user: CurrentUser):
    """F15: 库内 AI 问答——自然语言 → 结构化筛选 JSON → 查询影片库。

    LLM 不可用时降级为关键词规则提取（番号/评分/标签）。
    """
    import json
    import re
    from sqlalchemy import select
    from models import Task
    from services.ai_service import _get_cached, _hash_prompt, _save_cache, chat

    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    # A1: 历史参与缓存键与提示（避免不同上下文命中同一缓存）
    history = req.history or []
    history_snippet = ""
    if history:
        import json as _json
        recent = history[-6:]
        lines = []
        for h in recent:
            role = "用户" if h.get("role") == "user" else "助手"
            lines.append(f"{role}: {str(h.get('content', ''))[:120]}")
        history_snippet = "\n".join(lines)
    key = _hash_prompt(f"ask:{question}|{history_snippet[:300]}")
    cached = _get_cached(key)
    query: dict | None = None
    if cached:
        try:
            query = json.loads(cached)
        except Exception:
            query = None

    engine = "cache" if query else "ai"
    if query is None:
        schema = (
            '{"video_code": "精确番号或 null", "title_keyword": "标题关键词或 null", '
            '"rating_min": 最低评分数字或 null, "tags": ["标签数组，可为空"], '
            '"actors": ["演员数组，可为空"], "maker": "厂商或 null", '
            '"label": "厂牌或 null", "series": "系列或 null", '
            '"release_after": "YYYY-MM-DD 或 null", "view_status": "viewed|want|null", '
            '"sort": "rating|release_date|id", "limit": 20}'
        )
        prompt = (
            "你是一个影片库查询助手。把用户的自然语言问题转换成 JSON 筛选条件，只输出 JSON，不要其它文字。\n"
            + (f"以下是之前的对话（追问会基于上文条件叠加）：\n{history_snippet}\n" if history_snippet else "")
            + "\n"
            f"可用字段（全部可选，未知就 null）：{schema}\n"
            f"用户问题：{question}\n"
            "注意：标签/演员数组最多 5 个元素；评分按 0-10 分制；番号需大写（如 ABC-123）。"
        )
        try:
            raw = await chat(
                [
                    {"role": "system", "content": "你是影片库查询助手，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                task_type="ask",
            )
            m = re.search(r"\{[\s\S]*\}", raw or "")
            if m:
                parsed = json.loads(m.group(0))
                if isinstance(parsed, dict):
                    query = parsed
                    _save_cache(key, "ask", "", f"ask:{question}", m.group(0))
                    engine = "ai"
        except Exception:
            query = None

    # LLM 兜底：规则提取
    if query is None:
        engine = "rules"
        query = {}
        m = re.search(r"[A-Z]{2,5}-\d{2,5}[A-Z0-9]?", question.upper())
        if m:
            query["video_code"] = m.group(0)
        m = re.search(r"(\d+(?:\.\d+)?)\s*分", question)
        if m:
            query["rating_min"] = float(m.group(1))
        for tag in ("无码", "中出", "巨乳", "熟女", "萝莉", "人妻", "OL", "制服", "凌辱", "足交"):
            if tag in question:
                query.setdefault("tags", []).append(tag)
        query.setdefault("limit", 20)

    # 执行查询
    stmt = select(Task).where(Task.video_code.isnot(None))
    vc = query.get("video_code")
    if vc:
        stmt = stmt.where(Task.video_code == str(vc).upper())
    tk = query.get("title_keyword")
    if tk:
        stmt = stmt.where(Task.title.like(f"%{tk}%"))
    rm = query.get("rating_min")
    if rm is not None:
        try:
            stmt = stmt.where(Task.rating >= float(rm))
        except Exception:
            pass
    for t in (query.get("tags") or [])[:5]:
        if t:
            stmt = stmt.where(Task.tags.like(f"%{t}%"))
    for a in (query.get("actors") or [])[:5]:
        if a:
            stmt = stmt.where(Task.actors.like(f"%{a}%"))
    mk = query.get("maker")
    if mk:
        stmt = stmt.where(Task.maker.like(f"%{mk}%"))
    lb = query.get("label")
    if lb:
        stmt = stmt.where(Task.label.like(f"%{lb}%"))
    sr = query.get("series")
    if sr:
        stmt = stmt.where(Task.series.like(f"%{sr}%"))
    ra = query.get("release_after")
    if ra:
        stmt = stmt.where(Task.release_date >= str(ra))
    vs = query.get("view_status")
    if vs in ("viewed", "want"):
        stmt = stmt.where(Task.view_status == vs)
    sort = query.get("sort")
    if sort == "rating":
        stmt = stmt.order_by(Task.rating.desc())
    elif sort == "release_date":
        stmt = stmt.order_by(Task.release_date.desc())
    else:
        stmt = stmt.order_by(Task.id.desc())
    try:
        limit = min(int(query.get("limit") or 20), 50)
    except Exception:
        limit = 20
    rows = db.execute(stmt.limit(limit)).scalars().all()
    items = [
        {
            "task_id": t.id, "video_code": t.video_code, "title": t.title,
            "rating": t.rating, "poster_url": t.poster_url, "tags": t.tags, "actors": t.actors,
        }
        for t in rows
    ]
    return {"ok": True, "question": question, "query": query, "total": len(items), "items": items, "engine": engine}


@router.post("/batch-tags")
async def batch_tags(db: DbSession, _user: CurrentAdmin, limit: int = Query(5, le=20)):
    """N17: 批量补标签——取缺 ai_tags 的已入库任务，限速逐个调 LLM 生成。"""
    import logging
    from sqlalchemy import select
    from models import Task
    from services.ai_service import generate_tags

    logger = logging.getLogger("avdb.ai")
    rows = db.execute(
        select(Task).where(
            Task.status == "visited",
            (Task.ai_tags.is_(None) | (Task.ai_tags == "")),
            Task.title.isnot(None),
        ).order_by(Task.id.desc()).limit(limit)
    ).scalars().all()
    done = 0
    errors = 0
    for t in rows:
        try:
            tags = await generate_tags(t.title or t.video_code or "")
            if tags:
                t.ai_tags = tags
                done += 1
        except Exception as e:
            logger.warning("批量打标失败 task=%s: %s", t.id, e)
            errors += 1
    db.commit()
    return {"ok": True, "processed": len(rows), "done": done, "errors": errors}
