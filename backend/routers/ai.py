"""AI 路由 —— 翻译/标签/摘要/任务增强/V3 耳语情话。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from deps import CurrentUser
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
