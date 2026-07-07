"""AI 路由 —— 翻译/标签/摘要/任务增强。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from deps import CurrentUser
from services.ai_service import enrich_task, generate_tags, summarize, translate

router = APIRouter(prefix="/api/ai", tags=["ai"])


class TranslateRequest(BaseModel):
    text: str
    model: str | None = None


class TagsRequest(BaseModel):
    text: str
    model: str | None = None


class SummaryRequest(BaseModel):
    text: str
    model: str | None = None


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
