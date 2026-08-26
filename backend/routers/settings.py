"""配置中心路由 —— settings 表 CRUD。

安全设计（修 AVDB P0-4 密码脱敏覆盖 bug）：
- GET 时排除敏感字段（password/token/secret/key）
- PUT 时检测 *** 哨兵值，跳过（不覆盖真实值）
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from deps import CurrentAdmin, CurrentUser, DbSession
from models import Setting

router = APIRouter(prefix="/api/settings", tags=["settings"])

# 敏感字段：GET 时排除，PUT 时跳过哨兵值
from utils import is_sensitive_key as _is_sensitive  # noqa: E402


@router.get("")
def get_settings(db: DbSession, _user: CurrentUser):
    """读取全部配置（排除敏感字段值）。"""
    rows = db.execute(select(Setting)).scalars().all()
    result = {}
    for r in rows:
        result[r.key] = "***" if _is_sensitive(r.key) else r.value
    return result


@router.put("")
def update_settings(payload: dict, db: DbSession, _user: CurrentAdmin):
    """批量更新配置。值含 *** 的敏感字段跳过（哨兵值保护）。"""
    updated = 0
    skipped = 0
    for key, value in payload.items():
        if _is_sensitive(key) and value == "***":
            skipped += 1
            continue
        row = db.get(Setting, key)
        if row:
            row.value = str(value) if value is not None else ""
        else:
            db.add(Setting(key=key, value=str(value) if value is not None else ""))
        updated += 1
    db.commit()
    return {"ok": True, "updated": updated, "skipped_sentinel": skipped}


@router.get("/{key}")
def get_setting(key: str, db: DbSession, _user: CurrentUser):
    """读取单个配置。"""
    row = db.get(Setting, key)
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="配置项不存在")
    return {"key": key, "value": "***" if _is_sensitive(key) else row.value}


# ── Phase 1 补端点：备份/恢复 ──

@router.post("/backup")
def backup_settings(db: DbSession, _user: CurrentUser):
    """导出全量设置为 JSON（兼容 AVDB 前端）。"""
    import json
    rows = db.execute(select(Setting)).scalars().all()
    data = {r.key: ("***" if _is_sensitive(r.key) else r.value) for r in rows}
    return {"settings": data, "exported_at": str(datetime.utcnow())}


@router.post("/restore")
async def restore_settings(file: UploadFile, db: DbSession, _user: CurrentAdmin):
    """恢复设置：上传备份导出文件（JSON：{"settings": {...}}），脱敏值（***）跳过。

    F1 实现：替代原 501 占位；敏感 key 保持哨兵语义，不会覆盖为占位符。
    """
    import json
    raw = await file.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="无效的备份文件（需要 JSON 格式的设置导出）")
    settings_data = data.get("settings", data) if isinstance(data, dict) else None
    if not isinstance(settings_data, dict) or not settings_data:
        raise HTTPException(status_code=400, detail="备份文件缺少 settings 对象")
    updated = 0
    for key, value in settings_data.items():
        if _is_sensitive(key) and str(value) == "***":
            continue  # 脱敏值跳过，不覆盖真实凭据
        row = db.get(Setting, key)
        if row:
            row.value = str(value) if value is not None else ""
        else:
            db.add(Setting(key=key, value=str(value) if value is not None else ""))
        updated += 1
    db.commit()
    return {"ok": True, "restored": updated}


@router.delete("/clean-failed")
def clean_failed(db: DbSession, _user: CurrentUser):
    """清理所有失败任务。"""
    from models import Task
    deleted = db.execute(
        Task.__table__.delete().where(Task.status == "failed")
    ).rowcount
    db.commit()
    return {"ok": True, "deleted": deleted}


# ── 代理测试 ──

class ProxyTestRequest(BaseModel):
    proxy: str


@router.post("/test-proxy")
async def test_proxy(req: ProxyTestRequest, db: DbSession, _user: CurrentUser):
    """测试代理是否能访问 JavDB。

    优先用请求体中的 proxy；为空则读 DB settings 中的 http_proxy。
    """
    from config import get_settings

    proxy = (req.proxy or "").strip()
    if not proxy:
        row = db.get(Setting, "http_proxy")
        proxy = row.value.strip() if row and row.value else ""

    if not proxy:
        return {"ok": False, "message": "代理地址为空，请先填写代理地址"}

    javdb_url = get_settings().JAVDB_URL or "https://javdb.com"

    def _test_sync():
        import httpx
        # 用代理访问 JavDB，验证连通性
        # 注意：httpx 无法过 Cloudflare（403/503 是正常的），只要能收到
        # HTTP 响应就说明代理工作正常。实际爬取用 Playwright+stealth 过验证。
        with httpx.Client(proxy=proxy, timeout=15, follow_redirects=True) as client:
            r = client.get(javdb_url)
            return r.status_code, len(r.text)

    try:
        code, body_len = await asyncio.to_thread(_test_sync)
        # 能收到 HTTP 响应就说明代理通（200/403/503 都算连通）
        if code in (200, 403, 503):
            return {"ok": True, "message": f"代理连接正常 (HTTP {code})。实际爬取用 Playwright 过验证。"}
        return {"ok": True, "message": f"代理已连通 (HTTP {code}, 页面 {body_len} 字节)"}
    except Exception as e:
        return {"ok": False, "message": f"代理连接失败: {e}"}
