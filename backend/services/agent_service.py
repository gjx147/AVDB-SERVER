# -*- coding: utf-8 -*-
"""Agent 助手服务：对话意图 → 工具调用 → 自然语言回复。

- 读工具（查询/统计/配置读取）直接执行，LLM 汇总成自然语言
- 写工具（创建/删除/修改）两段式确认：预检生成 token → confirm(token) 执行
- 未配置 AI 时降级：检索/读取类工具直查，写工具提示需配置
"""
import json
import logging
import re
import time

from sqlalchemy import select

logger = logging.getLogger("avdb.agent_service")
from models import Task, Subscription, Setting, Rule

# ---------- 确认 token（无状态 HMAC 签名，10 分钟过期，多 worker 兼容） ----------
import base64
import hashlib
import hmac as hmac_mod
import json as _json

_TOKEN_TTL = 600


def _token_secret() -> bytes:
    """从持久化 SECRET_KEY 派生（多 worker 共享，重启不失效）。"""
    from config import get_settings
    sk = get_settings().SECRET_KEY or "avdb-agent-default-secret"
    return hashlib.sha256(("agent-confirm:" + sk).encode("utf-8")).digest()


def _issue_token(tool: str, args: dict, user: str = "ai") -> str:
    payload = {"tool": tool, "args": args, "user": user, "exp": int(time.time()) + _TOKEN_TTL}
    body = base64.urlsafe_b64encode(_json.dumps(payload, ensure_ascii=False).encode("utf-8")).rstrip(b"=").decode()
    sig = hmac_mod.new(_token_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def _consume_token(token: str, user: str | None = None) -> dict | None:
    """验签 + 过期检查 + （可选）签发人绑定校验。"""
    try:
        body, sig = token.split(".", 1)
    except Exception:
        return None
    expect = hmac_mod.new(_token_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac_mod.compare_digest(sig, expect):
        return None
    try:
        payload = _json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:
        return None
    if int(payload.get("exp") or 0) < time.time():
        return None
    if user and user != "anonymous" and payload.get("user") != user:
        return None  # token 绑定签发用户
    return {"tool": payload.get("tool"), "args": payload.get("args") or {}}


# ---------- 敏感配置判定（与 settings 路由一致） ----------
_SENSITIVE_PATTERNS = ("password", "token", "secret", "key", "apikey", "api_key",
                       "cookie", "session", "passwd", "credential", "auth", "jwt")


def _is_sensitive(key: str) -> bool:
    return any(p in key.lower() for p in _SENSITIVE_PATTERNS)


# S2: AI 可写配置白名单（默认拒绝；新增低风险 key 在此登记）
AI_WRITABLE_KEYS = {
    "actor_inactive_days",   # 演员休眠判定阈值
    "emby_auto_sync",        # Emby 自动同步开关
    "s3_backup_enabled",     # S3 备份开关
}


# ---------- 工具执行体 ----------
def _normalize_query(query: dict) -> dict:
    """S6: 规范化 LLM 输出的筛选条件（数组/数值/日期/长度钳制）。"""
    import datetime as _dt
    q = dict(query or {})
    for k in ("tags", "actors"):
        v = q.get(k)
        if isinstance(v, str):
            v = [x.strip() for x in v.split(",") if x.strip()]
        if not isinstance(v, list):
            v = []
        q[k] = [str(x).strip()[:20] for x in v[:5]]
    rm = q.get("rating_min")
    try:
        rm = float(rm)
        q["rating_min"] = min(max(rm, 0.0), 10.0)
    except (TypeError, ValueError):
        q.pop("rating_min", None)
    ra = q.get("release_after")
    if ra:
        try:
            _dt.datetime.strptime(str(ra)[:10], "%Y-%m-%d")
            q["release_after"] = str(ra)[:10]
        except ValueError:
            q.pop("release_after", None)
    try:
        q["limit"] = min(max(int(q.get("limit") or 20), 1), 50)
    except (TypeError, ValueError):
        q["limit"] = 20
    for k in ("video_code", "title_keyword", "maker", "label", "series"):
        if q.get(k) is not None:
            q[k] = str(q[k])[:100]
    return q


async def _parse_question(question: str):
    """NL → 筛选 JSON（LLM 优先，规则降级）。返回 (query, engine)。"""
    from services.ai_service import _get_cached, _hash_prompt, chat
    key = _hash_prompt(f"ask:{question}")
    cached = _get_cached(key)
    query = None
    if cached:
        try:
            query = json.loads(cached)
        except Exception:
            query = None
    engine = "cache" if query else "ai"
    if query is None:
        schema = (
            '{"video_code": "番号或 null", "title_keyword": "标题关键词或 null", '
            '"rating_min": 最低评分数字或 null, "tags": ["标签数组，可为空"], '
            '"actors": ["演员数组，可为空"], "maker": "厂商或 null", '
            '"label": "厂牌或 null", "series": "系列或 null", '
            '"release_after": "YYYY-MM-DD 或 null", "view_status": "viewed|want|null", '
            '"sort": "rating|release_date|id", "limit": 20}'
        )
        prompt = (
            "你是一个影片库查询助手。把用户的自然语言问题转换成 JSON 筛选条件，只输出 JSON，不要其它文字。\n"
            f"可用字段（全部可选，未知填 null）：{schema}\n"
            f"用户问题：{question}\n"
            "注意：标签/演员数组最多 5 个元素；评分按 0-10 分制；番号需大写（如 ABC-123）。"
        )
        try:
            raw = await chat(
                [{"role": "system", "content": "你是影片库助手，只输出 JSON。"},
                 {"role": "user", "content": prompt}],
                task_type="agent",
            )
            m = re.search(r"\{[\s\S]*\}", raw or "")
            if m:
                parsed = json.loads(m.group(0))
                if isinstance(parsed, dict):
                    query = parsed
                    engine = "ai"
        except Exception:
            query = None
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
    return _normalize_query(query), engine


def _search(db, args, query=None, engine=""):
    """执行筛选查询（query 缺省时规则降级提取）。"""
    question = str(args.get("question") or "").strip()
    if not question:
        return {"ok": False, "message": "问题不能为空"}
    if query is None:
        query, engine = {}, "rules"
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

    stmt = select(Task).where(Task.video_code.isnot(None))
    vc = query.get("video_code")
    if vc:
        stmt = stmt.where(Task.video_code == str(vc).upper())
    tk = query.get("title_keyword")
    if tk:
        esc = tk.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Task.title.like(f"%{esc}%", escape="\\"))
    rm = query.get("rating_min")
    if rm is not None:
        try:
            stmt = stmt.where(Task.rating >= float(rm))
        except Exception:
            pass
    for t in (query.get("tags") or [])[:5]:
        if t:
            esc = str(t).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            stmt = stmt.where(Task.tags.like(f"%{esc}%", escape="\\"))
    for a in (query.get("actors") or [])[:5]:
        if a:
            esc = str(a).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            stmt = stmt.where(Task.actors.like(f"%{esc}%", escape="\\"))
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
        {"task_id": t.id, "video_code": t.video_code, "title": t.title,
         "rating": t.rating, "poster_url": t.poster_url, "tags": t.tags,
         "actors": t.actors, "view_status": t.view_status}
        for t in rows
    ]
    return {"ok": True, "engine": engine, "query": query, "total": len(items), "items": items}


def _video_detail(db, args):
    tid = args.get("task_id")
    vc = args.get("video_code")
    t = None
    if tid:
        t = db.get(Task, int(tid))
    elif vc:
        t = db.execute(select(Task).where(Task.video_code == str(vc).upper())).scalars().first()
    if not t:
        return {"ok": False, "message": "作品不存在"}
    return {"ok": True, "item": {
        "task_id": t.id, "video_code": t.video_code, "title": t.title,
        "rating": t.rating, "tags": t.tags, "actors": t.actors, "maker": t.maker,
        "label": t.label, "series": t.series, "release_date": str(t.release_date or ""),
        "view_status": t.view_status, "note": t.note,
    }}


def _stats(db, args):
    total = db.execute(select(Task.id)).scalars().all()
    viewed = db.execute(select(Task.id).where(Task.view_status == "viewed")).scalars().all()
    want = db.execute(select(Task.id).where(Task.view_status == "want")).scalars().all()
    fav = db.execute(select(Task.id).where(Task.is_favorite == True)).scalars().all()  # noqa: E712
    rated = db.execute(select(Task.id).where(Task.rating.isnot(None))).scalars().all()
    subs = db.execute(select(Subscription.id)).scalars().all()
    rules = db.execute(select(Rule.id)).scalars().all()
    return {"ok": True, "stats": {
        "total": len(total), "viewed": len(viewed), "want": len(want),
        "favorites": len(fav), "rated": len(rated),
        "subscriptions": len(subs), "rules": len(rules),
    }}


def _actor_search(db, args):
    from models import Actor
    name = str(args.get("name") or "").strip()
    if not name:
        return {"ok": False, "message": "需要演员名"}
    rows = db.execute(select(Actor).where(Actor.name.like(f"%{name}%")).limit(10)).scalars().all()
    items = [{"actor_id": a.id, "name": a.name, "works_count": getattr(a, "works_count", None),
              "is_followed": getattr(a, "is_followed", None)} for a in rows]
    return {"ok": True, "items": items}


def _subscription_list(db, args):
    rows = db.execute(select(Subscription).order_by(Subscription.id)).scalars().all()
    return {"ok": True, "items": [
        {"id": s.id, "sub_type": s.sub_type, "name": s.name, "enabled": s.enabled,
         "rank_type": getattr(s, "rank_type", None), "actor_id": getattr(s, "actor_id", None)}
        for s in rows]}


def _rule_list(db, args):
    rows = db.execute(select(Rule).order_by(Rule.id)).scalars().all()
    return {"ok": True, "items": [
        {"id": r.id, "name": getattr(r, "name", None), "task_type": getattr(r, "task_type", None),
         "enabled": getattr(r, "enabled", True)}
        for r in rows]}


def _config_get(db, args):
    rows = db.execute(select(Setting)).scalars().all()
    data = {r.key: ("***" if _is_sensitive(r.key) else r.value) for r in rows}
    return {"ok": True, "settings": data}


# ---------- 写工具（确认制执行体） ----------
def _subscription_create(db, args):
    from routers.subscriptions import SubscriptionCreate, VALID_TYPES
    payload = SubscriptionCreate(**{k: v for k, v in args.items() if k in SubscriptionCreate.model_fields})
    if payload.sub_type not in VALID_TYPES:
        return {"ok": False, "message": f"无效类型，可选 {VALID_TYPES}"}
    if payload.sub_type == "ranking" and not payload.rank_type:
        return {"ok": False, "message": "ranking 类型需指定 rank_type"}
    if payload.sub_type == "actor" and not payload.actor_id:
        return {"ok": False, "message": "actor 类型需指定 actor_id"}
    sub = Subscription(**payload.model_dump())
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return {"ok": True, "message": f"订阅已创建 #{sub.id}：{sub.name}", "id": sub.id}


def _subscription_delete(db, args):
    sid = int(args.get("subscription_id") or 0)
    sub = db.get(Subscription, sid)
    if not sub:
        return {"ok": False, "message": "订阅不存在"}
    name = sub.name
    db.delete(sub)
    db.commit()
    return {"ok": True, "message": f"订阅已删除：{name}"}


def _subscription_toggle(db, args):
    sid = int(args.get("subscription_id") or 0)
    sub = db.get(Subscription, sid)
    if not sub:
        return {"ok": False, "message": "订阅不存在"}
    sub.enabled = not sub.enabled
    db.commit()
    return {"ok": True, "message": f"订阅「{sub.name}」已{'启用' if sub.enabled else '停用'}"}


def _rule_create(db, args):
    from routers.rules import RuleCreate
    payload = RuleCreate(**{k: v for k, v in args.items() if k in RuleCreate.model_fields})
    r = Rule(**payload.model_dump())
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"ok": True, "message": f"规则已创建 #{r.id}", "id": r.id}


def _rule_delete(db, args):
    rid = int(args.get("rule_id") or 0)
    r = db.get(Rule, rid)
    if not r:
        return {"ok": False, "message": "规则不存在"}
    db.delete(r)
    db.commit()
    return {"ok": True, "message": "规则已删除"}


def _set_view_status(db, args):
    tid = int(args.get("task_id") or 0)
    t = db.get(Task, tid)
    if not t:
        return {"ok": False, "message": "作品不存在"}
    status = args.get("view_status")
    if status not in ("viewed", "want", "none"):
        return {"ok": False, "message": "view_status 需为 viewed/want/none"}
    t.view_status = None if status == "none" else status
    db.commit()
    return {"ok": True, "message": f"{t.video_code} 已标记为{'看过' if status == 'viewed' else '想看' if status == 'want' else '未看'}"}


def _config_set(db, args):
    key = str(args.get("key") or "").strip()
    value = args.get("value")
    operator = str(args.get("_operator") or "ai")
    if not key:
        return {"ok": False, "message": "需要配置 key"}
    if _is_sensitive(key):
        return {"ok": False, "message": "敏感配置（密码/令牌/凭据）请在设置页手动修改，AI 仅可读"}
    if key not in AI_WRITABLE_KEYS:
        return {"ok": False, "message": f"配置 {key} 不在 AI 可写白名单，请在设置页手动修改"}
    row = db.get(Setting, key)
    old_value = row.value if row else None
    new_value = str(value) if value is not None else ""
    if old_value == new_value:
        return {"ok": True, "message": f"配置 {key} 无变化（已是 {new_value}）", "key": key, "old": old_value, "new": new_value}
    if row:
        row.value = new_value
    else:
        db.add(Setting(key=key, value=new_value))
    # G4: 审计留痕
    try:
        from models import ConfigAudit
        db.add(ConfigAudit(key=key, old_value=old_value, new_value=new_value, operator=operator, source="agent"))
    except Exception:
        pass
    db.commit()
    return {"ok": True, "message": f"配置 {key} 已更新", "key": key, "old": old_value, "new": new_value}


def _inspect(db, args):
    """配置巡检：缺失 / 异常 / 建议（静态检查器，不依赖 AI）"""
    from config import get_settings as get_env
    rows = db.execute(select(Setting)).scalars().all()
    data = {r.key: r.value for r in rows}
    env = get_env()
    problems = []
    tips = []
    if not data.get("AI_API_KEY") and not getattr(env, "AI_API_KEY", None):
        problems.append({"level": "error", "item": "AI_API_KEY", "detail": "未配置 AI Key，助手与智能功能不可用"})
    if data.get("AUTH_DISABLED", "").lower() == "true":
        problems.append({"level": "warning", "item": "AUTH_DISABLED", "detail": "认证已禁用，任何人均可访问，建议开启"})
    if not data.get("http_proxy") and not getattr(env, "HTTP_PROXY", None):
        tips.append("未配置代理，爬虫/下载可能受限")
    if not data.get("s3_bucket") and not getattr(env, "S3_BUCKET", None):
        tips.append("未配置 S3 备份，建议开启以防数据丢失")
    return {"ok": True, "problems": problems, "tips": tips}


async def _health_advice(db, args):
    from services.ai_reports import library_health_advice
    try:
        return await library_health_advice()
    except Exception as e:
        return {"ok": False, "message": str(e)}


async def _actor_dynamics(db, args):
    from services.ai_reports import actor_dynamics
    return await actor_dynamics(int(args.get("actor_id") or 0))


def _batch_set_view_status(db, args):
    """批量标记观看状态：按筛选条件（预解析 _query）或显式 task_ids。"""
    status = args.get("view_status")
    if status not in ("viewed", "want", "none"):
        return {"ok": False, "message": "view_status 需为 viewed/want/none"}
    task_ids = args.get("task_ids")
    if isinstance(task_ids, list) and task_ids:
        ids = [int(x) for x in task_ids[:500] if str(x).isdigit()]
        rows = db.execute(select(Task.id).where(Task.id.in_(ids))).scalars().all()
    else:
        query = args.get("_query")
        if not query:
            return {"ok": False, "message": "需要 task_ids 或查询条件 query_text"}
        rows = _search_rows(db, query)
    if not rows:
        return {"ok": False, "message": "没有匹配的作品"}
    from sqlalchemy import update as sa_update
    new_status = None if status == "none" else status
    db.execute(
        sa_update(Task)
        .where(Task.id.in_([t.id for t in rows]))
        .values(view_status=new_status)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    preview = [t.video_code or f"#{t.id}" for t in rows[:5]]
    return {"ok": True, "updated": len(rows), "preview_codes": preview,
            "message": f"已批量标记 {len(rows)} 部作品为{'看过' if status == 'viewed' else '想看' if status == 'want' else '未看'}"}


def _combo_mark_subscribe(db, args):
    """组合任务：按条件批量标记 + 创建订阅，一次确认。"""
    query_text = str(args.get("query_text") or "").strip()
    status = args.get("view_status") or "want"
    if status not in ("viewed", "want", "none"):
        return {"ok": False, "message": "view_status 需为 viewed/want/none"}

    query = args.get("_query")
    if query is None:
        return {"ok": False, "message": "条件解析失败（需要查询条件）"}
    rows = _search_rows(db, query)
    if not rows:
        return {"ok": False, "message": "没有匹配的作品，未执行任何操作"}

    from sqlalchemy import update as sa_update
    new_status = None if status == "none" else status
    db.execute(
        sa_update(Task).where(Task.id.in_([t.id for t in rows])).values(view_status=new_status)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    marked = len(rows)

    filters = {k: v for k, v in query.items() if k in ("makers", "labels", "series", "exclude_codes", "min_rating", "date_from") and v}
    if query.get("tags"):
        filters["genres"] = query["tags"]
    if query.get("actors"):
        filters.setdefault("genres", []).extend(query["actors"])
    name = str(args.get("sub_name") or "").strip() or f"AI 组合订阅（{query_text[:12] or '条件'}）"
    sub = Subscription(
        name=name[:100],
        sub_type="composite",
        filters_json=json.dumps(filters, ensure_ascii=False) if filters else None,
        auto_add=True,
        enabled=True,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return {"ok": True, "marked": marked, "subscription_id": sub.id, "subscription_name": sub.name,
            "message": f"已标记 {marked} 部 + 创建订阅「{sub.name}」"}


def _search_rows(db, query: dict) -> list:
    """按筛选条件取任务行（_search 的查询逻辑复用）。"""
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
    vs = query.get("view_status")
    if vs in ("viewed", "want"):
        stmt = stmt.where(Task.view_status == vs)
    try:
        limit = min(int(query.get("limit") or 50), 500)
    except Exception:
        limit = 500
    return db.execute(stmt.limit(limit)).scalars().all()


async def _preview_write(tool_name: str, args: dict, db) -> str:
    """写操作确认预览（批量/组合显示影响数与前 5 清单）。"""
    try:
        if tool_name in ("batch_set_view_status", "combo_mark_subscribe"):
            task_ids = args.get("task_ids")
            if isinstance(task_ids, list) and task_ids:
                ids = [int(x) for x in task_ids[:500] if str(x).isdigit()]
                rows = db.execute(select(Task.id).where(Task.id.in_(ids))).scalars().all()
            else:
                query = args.get("_query")
                rows = _search_rows(db, query) if query else []
            codes = [r.video_code or f"#{r.id}" for r in rows[:5]]
            head = "、".join(str(c) for c in codes) if codes else "（无）"
            extra = f"；将创建订阅「{str(args.get('sub_name') or '')[:20]}」" if tool_name == "combo_mark_subscribe" else ""
            return f"将影响 {len(rows)} 部作品（前 5：{head}）{extra}；确认后执行"
        return f"工具：{tool_name}；参数：{json.dumps(args, ensure_ascii=False)}"
    except Exception:
        return f"工具：{tool_name}；参数：{json.dumps(args, ensure_ascii=False)}"


# ---------- 工具注册表 ----------
TOOLS = [
    {"name": "search", "cn": "检索作品", "is_write": False,
     "desc": "按自然语言条件检索影片库（评分/标签/演员/番号/观看状态等），返回结果列表",
     "args": {"question": "自然语言检索条件，如：8分以上没看过的巨乳作品"},
     "handler": _search},
    {"name": "video_detail", "cn": "作品详情", "is_write": False,
     "desc": "查看单个作品详情（番号或 task_id）", "args": {"video_code": "番号", "task_id": "任务ID（可选）"},
     "handler": _video_detail},
    {"name": "stats", "cn": "库统计", "is_write": False,
     "desc": "影片库统计概览：总数/已看/想看/收藏/订阅/规则数量", "args": {},
     "handler": _stats},
    {"name": "actor_search", "cn": "演员查询", "is_write": False,
     "desc": "按名字查找演员", "args": {"name": "演员名"}, "handler": _actor_search},
    {"name": "subscription_list", "cn": "订阅列表", "is_write": False,
     "desc": "列出所有订阅", "args": {}, "handler": _subscription_list},
    {"name": "rule_list", "cn": "规则列表", "is_write": False,
     "desc": "列出所有自动规则", "args": {}, "handler": _rule_list},
    {"name": "config_get", "cn": "查看配置", "is_write": False,
     "desc": "读取系统配置（敏感值脱敏）", "args": {}, "handler": _config_get},
    {"name": "inspect", "cn": "系统巡检", "is_write": False,
     "desc": "巡检系统配置：缺失/异常/建议", "args": {}, "handler": _inspect},
    {"name": "health_advice", "cn": "库健康建议", "is_write": False,
     "desc": "影片库健康分 + 指标 + 3 条行动建议", "args": {}, "handler": _health_advice},
    {"name": "actor_dynamics", "cn": "演员动态解读", "is_write": False,
     "desc": "解读演员动态（活跃/休止/趋势）", "args": {"actor_id": "演员 ID"}, "handler": _actor_dynamics},
    # ---- 写工具 ----
    {"name": "subscription_create", "cn": "创建订阅", "is_write": True,
     "desc": "创建订阅（类型：actor=演员新作 / ranking=排行榜 / tag=标签筛选 / keyword=关键词）",
     "args": {"sub_type": "actor|ranking|tag|keyword", "name": "订阅名称",
              "actor_id": "actor 类型必填", "rank_type": "ranking 类型必填",
              "tags": "tag 类型标签数组", "keyword": "keyword 类型关键词"},
     "handler": _subscription_create},
    {"name": "subscription_delete", "cn": "删除订阅", "is_write": True,
     "desc": "删除指定订阅", "args": {"subscription_id": "订阅 ID"}, "handler": _subscription_delete},
    {"name": "subscription_toggle", "cn": "启用/停用订阅", "is_write": True,
     "desc": "切换订阅启用状态", "args": {"subscription_id": "订阅 ID"}, "handler": _subscription_toggle},
    {"name": "rule_create", "cn": "创建规则", "is_write": True,
     "desc": "创建自动处理规则（新作/下载等场景触发）",
     "args": {"name": "规则名", "task_type": "触发类型", "condition": "条件（可选）", "action": "动作（可选）"},
     "handler": _rule_create},
    {"name": "rule_delete", "cn": "删除规则", "is_write": True,
     "desc": "删除指定规则", "args": {"rule_id": "规则 ID"}, "handler": _rule_delete},
    {"name": "set_view_status", "cn": "标记观看状态", "is_write": True,
     "desc": "把作品标记为已看/想看/未看", "args": {"task_id": "作品任务 ID", "view_status": "viewed|want|none"},
     "handler": _set_view_status},
    {"name": "config_set", "cn": "修改配置", "is_write": True,
     "desc": "修改系统配置（敏感配置不可改）", "args": {"key": "配置键", "value": "新值"},
     "handler": _config_set},
    {"name": "batch_set_view_status", "cn": "批量标记", "is_write": True,
     "desc": "按筛选条件或指定 ID 批量标记观看状态（最多 500 部）",
     "args": {"query_text": "筛选条件（如：8分以上没看过的巨乳作品）", "task_ids": "作品 ID 数组（可选，二选一）",
              "view_status": "viewed|want|none"},
     "handler": _batch_set_view_status},
    {"name": "combo_mark_subscribe", "cn": "组合任务", "is_write": True,
     "desc": "按条件批量标记 + 创建同条件订阅，一次完成",
     "args": {"query_text": "筛选条件", "view_status": "viewed|want|none（默认 want）",
              "sub_name": "订阅名称（可选）"},
     "handler": _combo_mark_subscribe},
]

_TOOL_PROMPT = "\n".join(
    f"- {t['name']}（{t['cn']}，{'写操作需确认' if t['is_write'] else '只读'}）：{t['desc']}；参数：{json.dumps(t['args'], ensure_ascii=False)}"
    for t in TOOLS
)


def _pick_tool(name: str):
    for t in TOOLS:
        if t["name"] == name:
            return t
    return None


def _record_action(db, tool: str, args: dict, operator: str, result: str, ok: bool = True) -> None:
    """写操作审计留痕（参数快照 + 结果摘要）。"""
    try:
        from models import AgentAction
        db.add(AgentAction(
            tool=tool,
            args_json=json.dumps(args, ensure_ascii=False, default=str)[:2000],
            operator=operator or "ai",
            result=str(result)[:500],
            ok=ok,
        ))
        db.commit()
    except Exception:
        pass


def _undo_action(action_id: int, db, user) -> dict:
    """撤销写操作：可逆操作反转执行；删除类保留参数快照提示重建。"""
    from models import AgentAction
    a = db.get(AgentAction, action_id)
    if not a:
        return {"ok": False, "message": "操作记录不存在"}
    if a.undone:
        return {"ok": False, "message": "该操作已撤销过"}
    try:
        args = json.loads(a.args_json or "{}")
    except Exception:
        args = {}
    op = getattr(user, "username", None) or (user if isinstance(user, str) else "ai")

    if a.tool == "set_view_status":
        # 还原旧观看状态（记录旧值）
        old_status = args.get("_old_view_status")
        tid = int(args.get("task_id") or 0)
        t = db.get(Task, tid)
        if not t:
            return {"ok": False, "message": "作品不存在，无法撤销"}
        t.view_status = old_status or None
        db.commit()
        msg = f"已还原作品 #{tid} 观看状态"
    elif a.tool == "subscription_toggle":
        # 反转开关
        sid = int(args.get("subscription_id") or 0)
        sub = db.get(Subscription, sid)
        if not sub:
            return {"ok": False, "message": "订阅已不存在（可能已删除）"}
        sub.enabled = not sub.enabled
        db.commit()
        msg = f"订阅「{sub.name}」开关已反转"
    elif a.tool == "config_set":
        from models import Setting
        key = str(args.get("key") or "")
        old_value = args.get("_old_value")
        row = db.get(Setting, key)
        if row:
            row.value = old_value
        else:
            db.add(Setting(key=key, value=old_value))
        db.commit()
        msg = f"配置 {key} 已还原"
    else:
        # 删除类/创建类：参数快照已留，提示手动重建
        return {"ok": False, "message": f"该操作（{a.tool}）不可自动撤销；参数快照已保留，可据此手动恢复",
                "args_snapshot": args}

    a.undone = True
    db.commit()
    _record_action(db, f"undo_{a.tool}", {"action_id": action_id}, op, msg)
    return {"ok": True, "message": msg}


async def _run_read_tool(t, db, args):
    import inspect
    try:
        h = t["handler"]
        if inspect.iscoroutinefunction(h):
            return await h(db, args)
        return h(db, args)
    except Exception as e:
        logger.warning("读工具 %s 执行失败: %s", t["name"], e)
        return {"ok": False, "message": "工具执行失败，请稍后重试或查看日志"}


# ---------- Agent 主流程 ----------
async def _execute_step(t, tool_name, args, reason, db, user, llm_available) -> dict:
    """执行单步：写工具→确认卡片；读工具→执行并返回文本结果（供回喂）。"""
    if t["is_write"]:
        if not llm_available:
            return {"ok": False, "type": "error", "content": "写操作需要 AI 配置（设置页填写 AI_API_KEY）后使用"}
        token = _issue_token(tool_name, args, user=getattr(user, "username", None) or (user if isinstance(user, str) else "ai"))
        # 批量/组合工具：async 层预解析条件，供预览与执行共用
        if tool_name in ("batch_set_view_status", "combo_mark_subscribe") \
                and args.get("query_text") and not args.get("task_ids"):
            try:
                _q, _e = await _parse_question(str(args.get("query_text") or ""))
                args = dict(args)
                args["_query"] = _q
            except Exception:
                pass
        # 批量/组合工具预检：展示影响数量与清单
        preview = await _preview_write(tool_name, args, db)
        return {"ok": True, "type": "confirm", "tool": tool_name, "tool_cn": t["cn"],
                "args": args, "reason": reason, "preview": preview, "token": token}

    if tool_name == "search":
        try:
            query, engine = await _parse_question(str(args.get("question") or ""))
            result = _search(db, args, query=query, engine=engine)
        except Exception:
            result = _search(db, args)
    else:
        result = await _run_read_tool(t, db, args)

    if not result.get("ok"):
        return {"ok": True, "type": "answer", "content": f"执行失败：{result.get('message', '未知错误')}"}

    text = _result_to_text(tool_name, result)
    if tool_name == "search" and result.get("items"):
        return {"ok": True, "type": "answer", "content": text, "items": result.get("items"),
                "query": result.get("query"), "feed": text}
    return {"ok": True, "type": "answer", "content": text, "feed": text}


def _result_to_text(tool_name: str, result: dict) -> str:
    """工具结果 → 紧凑文本（回喂 LLM 与最终展示共用）。"""
    if tool_name == "search":
        items = result.get("items", [])
        if not items:
            return f"没有找到匹配的作品（引擎：{result.get('engine', '?')}）。可换个说法或放宽条件。"
        lines = [f"找到 {len(items)} 部："]
        for it in items[:10]:
            rating = f" {it['rating']}" if it.get("rating") else ""
            lines.append(f"- {it['video_code']}{rating} {(it.get('title') or '')[:30]}")
        return "\n".join(lines)
    if tool_name == "video_detail":
        it = result["item"]
        return (f"{it['video_code']} {it['title'] or ''}\n"
                f"评分：{it['rating'] or '-'} | 状态：{it['view_status'] or '未看'}\n"
                f"标签：{it['tags'] or '-'}\n演员：{it['actors'] or '-'}\n"
                f"厂商：{it['maker'] or '-'} | 厂牌：{it['label'] or '-'} | 系列：{it['series'] or '-'}\n"
                f"日期：{it['release_date'] or '-'}\n备注：{it['note'] or '-'}")
    if tool_name == "stats":
        st = result["stats"]
        return (f"库统计：共 {st['total']} 部 | 已看 {st['viewed']} | 想看 {st['want']} | "
                f"收藏 {st['favorites']} | 已评分 {st['rated']}\n订阅 {st['subscriptions']} 个 | 规则 {st['rules']} 条")
    if tool_name == "actor_search":
        items = result.get("items", [])
        return "没找到该演员" if not items else "\n".join(f"- {a['name']}（ID {a['actor_id']}）" for a in items)
    if tool_name == "subscription_list":
        items = result.get("items", [])
        return "还没有订阅，可以说「创建订阅」" if not items else "\n".join(
            f"- #{s['id']} {s['name']}（{s['sub_type']}，{'启用' if s['enabled'] else '停用'}）" for s in items)
    if tool_name == "rule_list":
        items = result.get("items", [])
        return "还没有规则" if not items else "\n".join(
            f"- #{r['id']} {r['name'] or r['task_type']}（{'启用' if r['enabled'] else '停用'}）" for r in items)
    if tool_name == "config_get":
        lines = [f"{k} = {v}" for k, v in list(result.get("settings", {}).items())[:40]]
        return "当前配置（敏感值已脱敏）：\n" + "\n".join(lines) if lines else "无配置"
    if tool_name == "inspect":
        probs = result.get("problems", [])
        tips = result.get("tips", [])
        if not probs and not tips:
            return "巡检通过：未发现问题"
        lines = [f"{'🔴' if p['level'] == 'error' else '🟡'} {p['item']}：{p['detail']}" for p in probs]
        lines += [f"💡 {tp}" for tp in tips]
        return "巡检结果：\n" + "\n".join(lines)
    return str(result)


def _agent_prompt(messages: list[dict], question: str) -> str:
    """Construct agent decision prompt: tool list + history + request."""
    history_block = ""
    recent = [m for m in messages if m.get("role") in ("user", "assistant")][-8:]
    if len(recent) > 1:
        lines = []
        for h in recent[:-1]:
            role = "User" if h.get("role") == "user" else "Assistant"
            lines.append(f"{role}: {str(h.get('content', ''))[:100]}")
        history_block = "Previous conversation:\n" + "\n".join(lines) + "\n"
    return (
        "You are the AVDB library assistant. Choose and call ONE tool per step.\n"
        f"Tools:\n{_TOOL_PROMPT}\n\n"
        f"{history_block}"
        f"User request: {question}\n\n"
        "Output JSON: {\"tool\": \"name\", \"args\": {...}, \"reason\": \"one-line note\"}\n"
        "Rules:\n"
        "1. Search/list/variant queries -> read-only tools (search/video_detail/stats/...)\n"
        "2. Create/delete/modify/mark -> write tools\n"
        "3. No relevant tool (greeting/small talk) -> {\"tool\": \"none\", \"args\": {}, \"reason\": \"chat\"}\n"
        "4. Complex questions may need multiple steps: call one tool, wait for the result, then decide next\n"
        "5. Output JSON only"
    )


async def agent_run(messages: list[dict], db, user) -> dict:
    """messages: [{'role','content'}...]，最后一条为用户请求。"""
    question = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            question = str(m.get("content") or "").strip()
            break
    if not question:
        return {"ok": False, "type": "error", "content": "请说点什么"}

    from services.ai_service import _get_cached, _hash_prompt, _save_cache, chat

    llm_available = True
    # E7: 消息内容截断（防超大 prompt/缓存膨胀）
    messages = [{"role": m.get("role"), "content": str(m.get("content") or "")[:2000]} for m in messages]
    question = question[:2000]
    history_block_key = ""
    recent = [m for m in messages if m.get("role") in ("user", "assistant")][-8:]
    if len(recent) > 1:
        history_block_key = "|" + "".join(str(m.get("content") or "")[:60] for m in recent[:-1])[:300]
    key = _hash_prompt(f"agent:{question}{history_block_key}")
    cached = _get_cached(key)
    decision = None
    if cached:
        try:
            decision = json.loads(cached)
        except Exception:
            decision = None

    if decision is None:
        prompt = _agent_prompt(messages, question)
        try:
            raw = await chat(
                [{"role": "system", "content": "你是 AVDB 影片库助手，只输出 JSON。"},
                 {"role": "user", "content": prompt}],
                task_type="agent",
            )
            m = re.search(r"\{[\s\S]*\}", raw or "")
            if m:
                decision = json.loads(m.group(0))
                _save_cache(key, "agent", "", f"agent:{question}", m.group(0))
        except Exception:
            decision = None
            llm_available = False
    else:
        llm_available = True

    if not decision or not isinstance(decision, dict):
        llm_available = False
        # 无 LLM 降级：关键词路由（读工具可直查；写工具需 AI 解析参数）
        q = question
        # 写意图优先（含"修改配置"类，避免被配置读取分支截胡）
        if any(k in q for k in ("创建订阅", "新建订阅", "删除订阅", "创建规则", "删除规则",
                                 "修改配置", "把", "改成", "设置为", "开启", "关闭", "标记为", "标为",
                                 "帮我订阅", "订阅一下")):
            return {"ok": False, "type": "error",
                    "content": "写操作需要 AI 配置（设置页填写 AI_API_KEY）后使用"}
        if any(k in q for k in ("统计", "多少部", "几部", "概览", "总览")):
            decision = {"tool": "stats", "args": {}, "reason": "关键词：统计"}
        elif "巡检" in q or "体检" in q:
            decision = {"tool": "inspect", "args": {}, "reason": "关键词：巡检"}
        elif "健康" in q and any(k in q for k in ("库", "建议", "怎么样", "如何", "状态")):
            decision = {"tool": "health_advice", "args": {}, "reason": "关键词：健康"}
        elif "动态" in q or "活跃" in q or "休止" in q:
            m = re.search(r"(\d+)", q)
            if m:
                decision = {"tool": "actor_dynamics", "args": {"actor_id": int(m.group(1))}, "reason": "关键词：动态"}
        elif any(k in q for k in ("查演员", "找演员", "演员")):
            name = re.sub(r"^(查|找|搜索|搜|看看|看下)?演员|演员", "", q).strip() or q.strip()
            decision = {"tool": "actor_search", "args": {"name": name}, "reason": "关键词：演员"}
        elif any(k in q for k in ("配置", "设置项", "当前设置")):
            decision = {"tool": "config_get", "args": {}, "reason": "关键词：配置"}
        elif "订阅" in q and any(k in q for k in ("列表", "哪些", "都有", "列出", "查看")):
            decision = {"tool": "subscription_list", "args": {}, "reason": "关键词：订阅列表"}
        elif "规则" in q and any(k in q for k in ("列表", "哪些", "都有", "列出", "查看")):
            decision = {"tool": "rule_list", "args": {}, "reason": "关键词：规则列表"}
        elif re.search(r"[A-Z]{2,5}-\d{2,5}", q.upper()) and ("详情" in q or "是什么" in q or "信息" in q):
            decision = {"tool": "video_detail", "args": {"video_code": re.search(r"[A-Z]{2,5}-\d{2,5}[A-Z0-9]?", q.upper()).group(0)}, "reason": "关键词：详情"}
        else:
            decision = {"tool": "search", "args": {"question": question}, "reason": "降级检索"}

    tool_name = str(decision.get("tool") or "none")
    if tool_name == "none":
        return {"ok": True, "type": "answer", "content": "我是你的库内助手：可以问我「8分以上的作品」「创建订阅」「查看统计」「把配置 XX 改成 YY」等。"}
    t = _pick_tool(tool_name)
    if not t:
        return {"ok": True, "type": "answer",
                "content": f"我没听懂（工具 {tool_name} 不存在），试试：「检索 8 分以上作品」「创建订阅」「查看统计」「巡检系统」。"}

    args = decision.get("args") or {}
    if not isinstance(args, dict):
        args = {}
    reason = str(decision.get("reason") or "")

    # ReAct 循环：读工具结果回喂 LLM 继续规划（最多 4 步）；写工具即止走确认
    steps: list[dict] = []
    loop_messages = list(messages)
    max_steps = 4
    for step_i in range(max_steps):
        step_result = await _execute_step(t, tool_name, args, reason, db, user, llm_available)

        if step_result.get("type") == "confirm":
            step_result["steps"] = steps
            return step_result  # 写操作：确认后终止，不链式执行

        if step_result.get("type") != "answer":
            step_result["steps"] = steps
            return step_result

        steps.append({"tool": tool_name, "reason": reason, "content": (step_result.get("content") or "")[:200]})

        # 已带最终展示数据（search 结果）→ 直接返回
        if step_result.get("items"):
            step_result["steps"] = steps
            return step_result

        feed = step_result.get("feed") or step_result.get("content") or ""
        if not feed or step_i == max_steps - 1:
            step_result["steps"] = steps
            return step_result

        # 回喂结果，让 LLM 决定下一步或终止
        loop_messages.append({"role": "assistant", "content": f"已执行「{tool_name}」：{feed[:500]}"})
        loop_messages.append({"role": "user", "content": "基于以上结果继续（如果已满足要求，输出 tool=none 并给出总结）"})
        decision = None
        try:
            raw = await chat(
                [{"role": "system", "content": "你是 AVDB 影片库助手，只输出 JSON。"},
                 {"role": "user", "content": _agent_prompt(loop_messages, question)}],
                task_type="agent",
            )
            m2 = re.search(r"\{[\s\S]*\}", raw or "")
            if m2:
                decision = json.loads(m2.group(0))
        except Exception:
            decision = None
        if not decision or not isinstance(decision, dict) or str(decision.get("tool") or "none") == "none":
            step_result["steps"] = steps
            return step_result
        tool_name = str(decision.get("tool") or "none")
        t = _pick_tool(tool_name)
        if not t:
            step_result["steps"] = steps
            return step_result
        args = decision.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        reason = str(decision.get("reason") or "")
    return {"ok": True, "type": "answer", "content": "已完成多步处理", "steps": steps}


def agent_confirm(token: str, db, user) -> dict:
    """确认执行写工具（token 绑定签发用户）。"""
    uname = getattr(user, "username", None) or (user if isinstance(user, str) else None)
    p = _consume_token(token, user=uname)
    if not p:
        return {"ok": False, "message": "确认已过期或无效（可能由其他用户发起），请重新发起"}
    t = _pick_tool(p["tool"])
    if not t or not t["is_write"]:
        return {"ok": False, "message": "无效工具"}
    args = dict(p["args"])
    args["_operator"] = uname or "ai"
    try:
        # config_set 撤销需旧值：预读并注入
        if t["name"] == "config_set" and args.get("key"):
            from models import Setting
            _row = db.get(Setting, str(args["key"]))
            args["_old_value"] = _row.value if _row else None
        if t["name"] == "set_view_status" and args.get("task_id"):
            _t = db.get(Task, int(args["task_id"]))
            args["_old_view_status"] = getattr(_t, "view_status", None) if _t else None
        result = t["handler"](db, args)
    except Exception as e:
        logger.warning("写工具 %s 执行失败: %s", t["name"], e)
        return {"ok": False, "message": "执行失败，请稍后重试或查看日志"}
    _record_action(db, t["name"], {k: v for k, v in args.items() if not k.startswith("_")},
                   uname or "ai", (result.get("message") if isinstance(result, dict) else str(result)),
                   ok=bool(result.get("ok", True)))
    return {"ok": True, "result": result}
