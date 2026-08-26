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


def _token_secret() -> bytes | None:
    """从持久化 SECRET_KEY 派生（多 worker 共享，重启不失效）。

    fail-closed：SECRET_KEY 缺失时返回 None，拒绝签发/验签（不落常量兜底）。
    """
    from config import get_settings
    sk = get_settings().SECRET_KEY
    if not sk:
        logger.error("SECRET_KEY 未配置，确认 token 不可用（fail-closed）")
        return None
    return hashlib.sha256(("agent-confirm:" + sk).encode("utf-8")).digest()


def _issue_token(tool: str, args: dict, user: str = "ai") -> str:
    if _token_secret() is None:
        raise RuntimeError("SECRET_KEY 未配置，无法签发确认 token")
    payload = {"tool": tool, "args": args, "user": user, "exp": int(time.time()) + _TOKEN_TTL}
    body = base64.urlsafe_b64encode(_json.dumps(payload, ensure_ascii=False).encode("utf-8")).rstrip(b"=").decode()
    sig = hmac_mod.new(_token_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


# E: token 已消费集合（防重复确认；上限 10000 条防内存膨胀）
_consumed_tokens: set[str] = set()
_CONSUMED_MAX = 10000


def _consume_token(token: str, user: str | None = None) -> dict | None:
    """验签 + 过期检查 + 签发人绑定 + 一次性消费（幂等）。"""
    try:
        body, sig = token.split(".", 1)
    except Exception:
        return None
    sec = _token_secret()
    if sec is None:
        return None
    expect = hmac_mod.new(sec, body.encode(), hashlib.sha256).hexdigest()[:32]
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
    if token in _consumed_tokens:
        return None  # 已消费：重复确认拒绝
    _consumed_tokens.add(token)
    if len(_consumed_tokens) > _CONSUMED_MAX:
        _consumed_tokens.clear()  # 简单防膨胀（TTL 内大量消费场景极少）
    return {"tool": payload.get("tool"), "args": payload.get("args") or {}}


# ---------- 敏感配置判定（与 settings 路由一致） ----------
def _is_sensitive(key: str) -> bool:
    """统一走 utils.is_sensitive_key（与 settings 路由共用一份清单）。"""
    from utils import is_sensitive_key
    return is_sensitive_key(key)


# S2: AI 可写配置白名单（默认拒绝；新增低风险 key 在此登记）
AI_WRITABLE_KEYS = {
    "actor_inactive_days",   # 演员休眠判定阈值
    "emby_auto_sync",        # Emby 自动同步开关
    "s3_backup_enabled",     # S3 备份开关
}


# ---------- 工具执行体 ----------
def _get_prefs(db, user: str | None = None) -> dict:
    """读取用户偏好（无记录返回空 dict）。"""
    try:
        from models import UserPref
        key = (getattr(user, "username", None) or (user if isinstance(user, str) else None)) or "default"
        row = db.get(UserPref, key)
        return json.loads(row.prefs_json or "{}") if row else {}
    except Exception:
        return {}


def _apply_prefs(query: dict, prefs: dict) -> dict:
    """偏好注入：用户未显式指定的字段用偏好默认。"""
    q = dict(query or {})
    if prefs.get("rating_min") is not None and q.get("rating_min") is None:
        q["rating_min"] = float(prefs["rating_min"])
    if prefs.get("tags") and not q.get("tags"):
        q["tags"] = list(prefs["tags"])[:5]
    if prefs.get("only_unviewed") and q.get("view_status") is None:
        q["view_status"] = "want"
    if prefs.get("sort") and q.get("sort") is None:
        q["sort"] = prefs["sort"]
    return q


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
            # 中英兼容：主标签+别名合并为 OR 条件组（任一命中即可）
            try:
                from sqlalchemy import or_
                from services.tag_translate import cn_aliases
                conds = [Task.tags.like(f"%{str(t).replace(chr(92), chr(92)*2).replace('%', chr(92)+'%').replace('_', chr(92)+'_')}%", escape=chr(92))]
                for a in cn_aliases(str(t))[1:]:
                    ea = str(a).replace(chr(92), chr(92)*2).replace('%', chr(92)+'%').replace('_', chr(92)+'_')
                    conds.append(Task.tags.like(f"%{ea}%", escape=chr(92)))
                stmt = stmt.where(or_(*conds) if len(conds) > 1 else conds[0])
            except Exception as e:
                logger.warning("标签别名匹配失败: %s", e)
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
    if payload.filters_json and len(payload.filters_json) > 5000:
        return {"ok": False, "message": "订阅条件过长"}
    sub = Subscription(**payload.model_dump())
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return {"ok": True, "message": f"订阅已创建 #{sub.id}：{sub.name}", "id": sub.id}


def _subscription_delete(db, args):
    sid = _int(args.get("subscription_id"))
    sub = db.get(Subscription, sid)
    if not sub:
        return {"ok": False, "message": "订阅不存在"}
    name = sub.name
    db.delete(sub)
    db.commit()
    return {"ok": True, "message": f"订阅已删除：{name}"}


def _subscription_toggle(db, args):
    sid = _int(args.get("subscription_id"))
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
    rid = _int(args.get("rule_id"))
    r = db.get(Rule, rid)
    if not r:
        return {"ok": False, "message": "规则不存在"}
    db.delete(r)
    db.commit()
    return {"ok": True, "message": "规则已删除"}


def _set_view_status(db, args):
    tid = _int(args.get("task_id"))
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


def _pref_get(db, args):
    """查看当前偏好。"""
    prefs = _get_prefs(db, args.get("_operator"))
    if not prefs:
        return {"ok": True, "prefs": {}, "message": "暂无偏好设置"}
    lines = [f"- {k} = {v}" for k, v in prefs.items()]
    return {"ok": True, "prefs": prefs, "message": "当前偏好：\n" + "\n".join(lines)}


def _pref_set(db, args):
    """设置偏好（写，确认制）。支持：rating_min/tags/only_unviewed/sort。"""
    from models import UserPref
    key = str(args.get("key") or "")
    value = args.get("value")
    valid_keys = {"rating_min", "tags", "only_unviewed", "sort"}
    if key not in valid_keys:
        return {"ok": False, "message": f"支持偏好：{'/'.join(valid_keys)}"}
    prefs = _get_prefs(db, args.get("_operator"))
    if key == "rating_min":
        try:
            prefs[key] = min(max(float(value), 0.0), 10.0)
        except (TypeError, ValueError):
            return {"ok": False, "message": "rating_min 需为 0-10 数字"}
    elif key == "tags":
        if isinstance(value, str):
            value = [x.strip() for x in value.split(",") if x.strip()]
        prefs[key] = [str(x)[:20] for x in (value or [])[:5]]
    elif key == "only_unviewed":
        prefs[key] = bool(value)
    elif key == "sort":
        prefs[key] = str(value)[:20]
    uname = args.get("_operator") or "default"
    row = db.get(UserPref, uname)
    if row:
        row.prefs_json = json.dumps(prefs, ensure_ascii=False)
    else:
        db.add(UserPref(user=uname, prefs_json=json.dumps(prefs, ensure_ascii=False)))
    db.commit()
    return {"ok": True, "message": f"偏好已更新：{key} = {prefs[key]}"}


def _pref_clear(db, args):
    """清除全部偏好。"""
    from models import UserPref
    uname = args.get("_operator") or "default"
    row = db.get(UserPref, uname)
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True, "message": "偏好已清空"}


async def _tag_translate(db, args):
    """标签翻译预览（英文→中文映射）。"""
    from services.tag_translate import tag_translate_preview
    try:
        return await tag_translate_preview()
    except Exception as e:
        return {"ok": False, "message": str(e)}


def _tag_translate_apply(db, args):
    """应用标签映射（英文→中文）。"""
    from services.tag_translate import tag_translate_apply
    mapping = args.get("mapping") or {}
    if not isinstance(mapping, dict):
        return {"ok": False, "message": "mapping 需为对象"}
    return tag_translate_apply(mapping)


def _fill_works(db, args):
    """全部补齐作品（后台任务）。"""
    try:
        from services import actor_works_batch
        wait = min(int(args.get("wait_limit_min") or 60), 600)
        ok, msg = actor_works_batch.start(wait_limit_min=wait, max_co_star=int(args.get("max_co_star") or 0))
        if not ok:
            return {"ok": False, "message": msg}
        return {"ok": True, "message": f"已启动全部补齐作品：{msg}"}
    except Exception as e:
        return {"ok": False, "message": f"启动失败：{e}"}


def _actor_crawl_works(db, args):
    """爬取单个演员的全部作品（后台线程）。"""
    from models import Actor
    actor_id = _int(args.get("actor_id"))
    actor = db.get(Actor, actor_id)
    if not actor:
        return {"ok": False, "message": "演员不存在"}
    url = actor.source_url or ""
    if not url and actor.note and actor.note.startswith("source_url: "):
        url = actor.note[len("source_url: "):]
    if not url:
        return {"ok": False, "message": f"演员 {actor.name} 无来源 URL，需先在演员库添加"}
    try:
        from services.new_works_monitor import check_actor_new_works
        import asyncio as _aio
        import threading
        def _run():
            try:
                _aio.run(check_actor_new_works(actor_id, auto_add=False))
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True, "message": f"已开始爬取 {actor.name} 的作品（后台进行）"}
    except Exception as e:
        return {"ok": False, "message": f"启动失败：{e}"}


def _collection_list(db, args):
    from models import Collection, task_collections
    rows = db.execute(select(Collection).order_by(Collection.id.desc()).limit(30)).scalars().all()
    items = []
    for c in rows:
        cnt = db.execute(select(task_collections.c.task_id).where(task_collections.c.collection_id == c.id)).scalars().all()
        items.append({"id": c.id, "name": c.name, "count": len(cnt)})
    return {"ok": True, "items": items}


def _collection_create(db, args):
    from models import Collection
    name = str(args.get("name") or "").strip()[:100]
    if not name:
        return {"ok": False, "message": "需要收藏夹名称"}
    c = Collection(name=name, description=str(args.get("description") or "")[:500] or None)
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"ok": True, "message": f"收藏夹「{c.name}」已创建", "id": c.id}


def _collection_add(db, args):
    from models import Collection, task_collections
    cid = _int(args.get("collection_id"))
    tid = _int(args.get("task_id"))
    c = db.get(Collection, cid)
    if not c:
        return {"ok": False, "message": "收藏夹不存在"}
    t = db.get(Task, tid)
    if not t:
        return {"ok": False, "message": "作品不存在"}
    exists = db.execute(select(task_collections.c.id).where(
        task_collections.c.collection_id == cid, task_collections.c.task_id == tid)).scalar()
    if exists:
        return {"ok": True, "message": f"已在收藏夹「{c.name}」中"}
    db.execute(task_collections.insert().values(collection_id=cid, task_id=tid))
    db.commit()
    return {"ok": True, "message": f"{t.video_code} 已加入收藏夹「{c.name}」"}


def _download_list(db, args):
    from models import Download
    rows = db.execute(select(Download).order_by(Download.id.desc()).limit(15)).scalars().all()
    items = [{"id": d.id, "video_code": d.video_code, "status": d.status,
              "progress": getattr(d, "progress", None), "downloader": d.downloader} for d in rows]
    return {"ok": True, "items": items}


def _notify_list(db, args):
    from models import NotifyLog
    rows = db.execute(select(NotifyLog).order_by(NotifyLog.id.desc()).limit(15)).scalars().all()
    items = [{"id": n.id, "event": n.event, "title": n.title, "ok": n.ok,
              "created_at": n.created_at.strftime("%Y-%m-%d %H:%M") if n.created_at else None} for n in rows]
    return {"ok": True, "items": items}


def _batch_retry(db, args):
    """批量重试失败任务。"""
    from sqlalchemy import update as sa_update
    limit = min(int(args.get("limit") or 50), 200)
    failed_ids = db.execute(
        select(Task.id).where(Task.status == "failed").limit(limit)
    ).scalars().all()
    if not failed_ids:
        return {"ok": True, "message": "没有失败任务可重试"}
    db.execute(
        sa_update(Task).where(Task.id.in_(failed_ids))
        .values(status="pending", error_message=None)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return {"ok": True, "message": f"已重新排队 {len(failed_ids)} 个失败任务"}


async def _magnet_search(db, args):
    """多源磁力搜索。"""
    from services.magnet_sources import search_all
    code = str(args.get("video_code") or "").strip().upper()
    if not code:
        return {"ok": False, "message": "需要番号"}
    results = await search_all(code, None)
    items = []
    for src in (results or []):
        if isinstance(src, dict):
            items.append({"source": src.get("source") or src.get("name") or "?",
                          "magnet": src.get("magnet") or "", "size": src.get("size") or "",
                          "title": src.get("title") or ""})
        elif isinstance(src, list):
            for it in src[:5]:
                items.append({"source": "?", "magnet": it.get("magnet") or "",
                              "size": it.get("size") or "", "title": it.get("title") or ""})
    if not items:
        return {"ok": False, "message": f"未找到 {code} 的磁力资源"}
    return {"ok": True, "video_code": code, "items": items[:10], "total": len(items)}


def _push_download(db, args):
    """推送单任务下载。"""
    from models import Task
    tid = _int(args.get("task_id"))
    t = db.get(Task, tid)
    if not t:
        return {"ok": False, "message": "作品不存在"}
    magnet = t.best_magnet
    if not magnet:
        return {"ok": False, "message": f"{t.video_code} 没有磁力链接，可先对话「搜索磁力」"}
    try:
        import asyncio as _aio
        import threading
        from services.download_strategy import push_to_downloader
        def _run():
            try:
                _aio.run(push_to_downloader(t.id, magnet))
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True, "message": f"已推送 {t.video_code} 下载（后台）"}
    except Exception as e:
        return {"ok": False, "message": f"推送失败：{e}"}


def _batch_push(db, args):
    """批量推送下载。"""
    from models import Task
    ids = [int(x) for x in (args.get("task_ids") or []) if str(x).isdigit()]
    if not ids:
        return {"ok": False, "message": "需要 task_ids"}
    rows = db.execute(select(Task).where(Task.id.in_(ids[:50]))).scalars().all()
    ok, skip = 0, 0
    import asyncio as _aio
    import threading
    from services.download_strategy import push_to_downloader
    for t in rows:
        if t.best_magnet:
            def _run(tid=t.id, mag=t.best_magnet):
                try:
                    _aio.run(push_to_downloader(tid, mag))
                except Exception:
                    pass
            threading.Thread(target=_run, daemon=True).start()
            ok += 1
        else:
            skip += 1
    return {"ok": True, "message": f"已推送 {ok} 部下载（{skip} 部无磁力跳过）"}


def _new_releases_list(db, args):
    from models import NewRelease
    stmt = select(NewRelease).order_by(NewRelease.release_date.desc().nullslast(), NewRelease.id.desc()).limit(20)
    aid = args.get("actor_id")
    if aid:
        stmt = stmt.where(NewRelease.actor_id == int(aid))
    if args.get("only_unread"):
        stmt = stmt.where(NewRelease.is_read == False)  # noqa: E712
    rows = db.execute(stmt).scalars().all()
    items = [{"id": r.id, "video_code": r.video_code, "title": getattr(r, "title", None),
              "release_date": str(r.release_date or ""), "actor_id": r.actor_id,
              "is_read": bool(getattr(r, "is_read", False))} for r in rows]
    return {"ok": True, "items": items}


def _new_release_add(db, args):
    from routers.new_releases import add_to_library_api
    nid = _int(args.get("new_release_id"))
    r = add_to_library_api(nid, db, "anonymous")
    return {"ok": True, "message": f"新作已加入库：{r.get('message', '')}"}


def _new_release_read(db, args):
    from models import NewRelease
    nid = _int(args.get("new_release_id"))
    r = db.get(NewRelease, nid)
    if not r:
        return {"ok": False, "message": "新作记录不存在"}
    r.is_read = True
    db.commit()
    return {"ok": True, "message": f"{r.video_code} 已标记已读"}


def _new_release_check_all(db, args):
    """全量检查所有订阅演员的新作（后台）。"""
    try:
        import asyncio as _aio
        import threading
        from routers.new_releases import check_all_now
        def _run():
            try:
                _aio.run(check_all_now(db, "anonymous"))
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True, "message": "已开始全量检查新作（后台进行）"}
    except Exception as e:
        return {"ok": False, "message": f"启动失败：{e}"}


def _ranking_list(db, args):
    from models import Ranking
    from sqlalchemy import func
    rank_type = str(args.get("rank_type") or "daily")
    if rank_type not in ("daily", "weekly", "monthly"):
        return {"ok": False, "message": "rank_type 需为 daily/weekly/monthly"}
    date = args.get("date")
    if not date:
        date = db.execute(select(func.max(Ranking.rank_date)).where(Ranking.rank_type == rank_type)).scalar_one()
    if not date:
        return {"ok": True, "items": [], "message": f"暂无 {rank_type} 榜单数据"}
    rows = db.execute(
        select(Ranking).where(Ranking.rank_type == rank_type, Ranking.rank_date == date)
        .order_by(Ranking.rank_position).limit(30)
    ).scalars().all()
    items = [{"id": r.id, "rank": r.rank_position, "video_code": getattr(r, "video_code", None),
              "title": getattr(r, "title", None), "rating": getattr(r, "rating", None)} for r in rows]
    return {"ok": True, "items": items, "rank_type": rank_type, "date": str(date)}


def _ranking_add_tasks(db, args):
    from models import Ranking, Task
    ids = [int(x) for x in (args.get("ranking_ids") or [])]
    if not ids:
        return {"ok": False, "message": "需要 ranking_ids（榜单条目 ID）"}
    rows = db.execute(select(Ranking).where(Ranking.id.in_(ids[:100]))).scalars().all()
    added = 0
    from models import ListSource
    src = db.execute(select(ListSource.id).limit(1)).scalar()
    for r in rows:
        code = getattr(r, "video_code", None) or ""
        if not code:
            continue
        exists = db.execute(select(Task.id).where(Task.video_code == code)).scalar()
        if exists:
            continue
        db.add(Task(list_source_id=src or 1, url=f"https://rank/{code}", video_code=code,
                    title=getattr(r, "title", None), status="pending"))
        added += 1
    db.commit()
    return {"ok": True, "message": f"已入库 {added} 条榜单条目（{len(rows) - added} 条已存在）", "added": added}


def _task_delete(db, args):
    tid = _int(args.get("task_id"))
    t = db.get(Task, tid)
    if not t:
        return {"ok": False, "message": "作品不存在"}
    code = t.video_code
    db.delete(t)
    db.commit()
    return {"ok": True, "message": f"已删除 {code or tid}"}


def _batch_delete(db, args):
    ids = [int(x) for x in (args.get("task_ids") or []) if str(x).isdigit()]
    if not ids:
        return {"ok": False, "message": "需要 task_ids"}
    deleted = db.execute(Task.__table__.delete().where(Task.id.in_(ids[:200]))).rowcount
    db.commit()
    return {"ok": True, "message": f"已删除 {deleted} 个任务"}


def _drive115_offline_add(db, args):
    from services.drive115_client import add_offline_task
    import asyncio as _aio
    magnet = str(args.get("magnet") or "").strip()
    if not magnet and args.get("task_id"):
        t = db.get(Task, int(args.get("task_id")))
        magnet = t.best_magnet if t else ""
    if not magnet:
        return {"ok": False, "message": "需要磁力链接或 task_id"}
    try:
        result = _aio.run(add_offline_task(magnet))
        return {"ok": True, "message": f"115 离线任务已添加：{result}"}
    except Exception as e:
        return {"ok": False, "message": f"115 添加失败：{e}"}


def _actor_detail(db, args):
    """演员档案详情。"""
    from models import Actor, actor_movies
    actor_id = _int(args.get("actor_id"))
    a = db.get(Actor, actor_id)
    if not a:
        return {"ok": False, "message": "演员不存在"}
    works = db.execute(select(actor_movies.c.task_id).where(actor_movies.c.actor_id == actor_id)).scalars().all()
    return {"ok": True, "actor": {
        "id": a.id, "name": a.name, "intro": getattr(a, "intro", None),
        "birth_date": getattr(a, "birth_date", None), "height": getattr(a, "height", None),
        "cup": getattr(a, "cup", None), "measurements": getattr(a, "measurements", None),
        "gender": getattr(a, "gender", None), "works_count": len(works),
        "is_followed": getattr(a, "is_followed", None),
    }}


def _actor_blacklist(db, args):
    """切换演员黑名单。"""
    from models import Actor
    actor_id = _int(args.get("actor_id"))
    a = db.get(Actor, actor_id)
    if not a:
        return {"ok": False, "message": "演员不存在"}
    current = bool(getattr(a, "blacklisted", False))
    a.blacklisted = not current
    db.commit()
    return {"ok": True, "message": f"已{'拉黑' if not current else '取消拉黑'}演员 {a.name}"}


def _filter_rule_list(db, args):
    """内容过滤规则列表。"""
    from models import ContentFilterRule
    rows = db.execute(select(ContentFilterRule).order_by(ContentFilterRule.id)).scalars().all()
    items = [{"id": r.id, "name": r.name, "keyword": r.keyword, "action": r.action,
              "enabled": bool(r.enabled)} for r in rows]
    return {"ok": True, "items": items}


def _filter_rule_create(db, args):
    """创建内容过滤规则。"""
    from models import ContentFilterRule
    name = str(args.get("name") or "").strip()[:100]
    keyword = str(args.get("keyword") or "").strip()[:200]
    if not name or not keyword:
        return {"ok": False, "message": "需要名称与关键词"}
    action = str(args.get("action") or "hide")[:20]
    if action not in ("hide", "block", "mark", "exclude"):
        action = "hide"
    r = ContentFilterRule(
        name=name, keyword=keyword,
        is_regex=bool(args.get("is_regex")), case_sensitive=bool(args.get("case_sensitive")),
        action=action,
        fields_json=str(args.get("fields_json") or "")[:500] or None,
        message=str(args.get("message") or "")[:200] or None,
        enabled=True,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"ok": True, "message": f"过滤规则「{r.name}」已创建", "id": r.id}


def _filter_rule_delete(db, args):
    """删除内容过滤规则。"""
    from models import ContentFilterRule
    rid = _int(args.get("rule_id"))
    r = db.get(ContentFilterRule, rid)
    if not r:
        return {"ok": False, "message": "规则不存在"}
    db.delete(r)
    db.commit()
    return {"ok": True, "message": f"过滤规则「{r.name}」已删除"}


def _task_dedupe(db, args):
    """去重：dry_run 默认预览，执行需显式 dry_run=false（确认制）。"""
    from routers.tasks import dedupe_tasks
    dry_run = bool(args.get("dry_run", True))
    try:
        r = dedupe_tasks(db, "anonymous", dry_run=dry_run)
        if dry_run:
            return {"ok": True, "dry_run": True, "message": f"发现 {r.get('groups', 0)} 组重复，可删除 {r.get('to_delete', 0)} 个任务（确认后执行）"}
        return {"ok": True, "message": f"已去重：删除 {r.get('deleted', 0)} 个重复任务"}
    except Exception as e:
        return {"ok": False, "message": f"去重失败：{e}"}


def _crawl_status(db, args):
    """爬虫运行状态。"""
    try:
        from services.scraper_lock import is_running, get_info
        info = get_info() or {}
        return {"ok": True, "running": is_running(), "owner": info.get("name") or info.get("cmd") or ""}
    except Exception:
        return {"ok": False, "message": "无法获取爬虫状态"}


def _crawl_control(db, args):
    """爬虫控制：pause/resume/stop。"""
    action = str(args.get("action") or "")
    if action not in ("pause", "resume", "stop"):
        return {"ok": False, "message": "action 需为 pause/resume/stop"}
    try:
        if action == "stop" or action == "pause":
            from routers.crawl import stop_crawl
            r = stop_crawl("anonymous")
            return {"ok": True, "message": f"爬虫已停止：{r.get('message', '')}"}
        from routers.crawl import resume_crawl
        r = resume_crawl("anonymous")
        return {"ok": True, "message": f"爬虫已恢复：{r.get('message', '')}"}
    except Exception as e:
        return {"ok": False, "message": f"控制失败：{e}"}


def _fill_works_status(db, args):
    """补齐作品进度。"""
    from services import actor_works_batch
    try:
        st = actor_works_batch.status()
        return {"ok": True, "status": st}
    except Exception as e:
        return {"ok": False, "message": f"查询失败：{e}"}


def _recommendations(db, args):
    """个性化推荐。"""
    from routers.insights import recommendations
    limit = min(int(args.get("limit") or 5), 10)
    try:
        r = recommendations(db, "anonymous", limit=limit)
        items = r.get("items") or []
        out = [{"task_id": it.get("task_id"), "video_code": it.get("video_code"),
                "title": it.get("title"), "rating": it.get("rating")} for it in items]
        return {"ok": True, "items": out}
    except Exception as e:
        return {"ok": False, "message": f"推荐失败：{e}"}


def _similar_works(db, args):
    """相似作品推荐。"""
    from routers.v2 import similar_tasks
    tid = _int(args.get("task_id"))
    limit = min(int(args.get("limit") or 5), 10)
    try:
        r = similar_tasks(tid, db, "anonymous", limit=limit)
        items = r.get("items") or []
        out = [{"task_id": it.get("task_id"), "video_code": it.get("video_code"),
                "title": it.get("title"), "rating": it.get("rating")} for it in items]
        return {"ok": True, "items": out}
    except Exception as e:
        return {"ok": False, "message": f"相似推荐失败：{e}"}


def _task_extract(db, args):
    """触发任务元数据提取（后台）。"""
    tid = _int(args.get("task_id"))
    t = db.get(Task, tid)
    if not t:
        return {"ok": False, "message": "作品不存在"}
    try:
        from routers.tasks import extract_single
        r = extract_single(tid, db, "anonymous")
        msg = r.get("message") if isinstance(r, dict) else "已触发提取"
        return {"ok": True, "message": f"{t.video_code} 提取已触发：{msg}"}
    except Exception as e:
        return {"ok": False, "message": f"提取失败：{e}"}


def _task_note(db, args):
    tid = _int(args.get("task_id"))
    t = db.get(Task, tid)
    if not t:
        return {"ok": False, "message": "作品不存在"}
    t.note = str(args.get("note") or "")[:2000] or None
    db.commit()
    return {"ok": True, "message": f"{t.video_code or tid} 备注已更新"}


def _task_favorite(db, args):
    tid = _int(args.get("task_id"))
    t = db.get(Task, tid)
    if not t:
        return {"ok": False, "message": "作品不存在"}
    fav = bool(args.get("favorite", True))
    t.is_favorite = fav
    if fav:
        from datetime import datetime as _dt
        t.favorite_at = _dt.utcnow()
    db.commit()
    return {"ok": True, "message": f"{t.video_code} 已{'收藏' if fav else '取消收藏'}"}


def _favorites_list(db, args):
    rows = db.execute(select(Task).where(Task.is_favorite == True).order_by(Task.favorite_at.desc()).limit(20)).scalars().all()  # noqa: E712
    items = [{"task_id": t.id, "video_code": t.video_code, "title": t.title, "rating": t.rating} for t in rows]
    return {"ok": True, "items": items}


def _collection_delete(db, args):
    from models import Collection
    cid = _int(args.get("collection_id"))
    c = db.get(Collection, cid)
    if not c:
        return {"ok": False, "message": "收藏夹不存在"}
    db.delete(c)
    db.commit()
    return {"ok": True, "message": f"收藏夹「{c.name}」已删除"}


def _collection_remove(db, args):
    from models import Collection, task_collections
    cid = _int(args.get("collection_id"))
    tid = _int(args.get("task_id"))
    c = db.get(Collection, cid)
    if not c:
        return {"ok": False, "message": "收藏夹不存在"}
    db.execute(task_collections.delete().where(
        task_collections.c.collection_id == cid, task_collections.c.task_id == tid))
    db.commit()
    return {"ok": True, "message": f"作品 #{tid} 已移出收藏夹「{c.name}」"}


def _collection_tasks(db, args):
    from models import Collection, task_collections
    cid = _int(args.get("collection_id"))
    c = db.get(Collection, cid)
    if not c:
        return {"ok": False, "message": "收藏夹不存在"}
    rows = db.execute(
        select(Task).join(task_collections, task_collections.c.task_id == Task.id)
        .where(task_collections.c.collection_id == cid).limit(20)
    ).scalars().all()
    items = [{"task_id": t.id, "video_code": t.video_code, "title": t.title, "rating": t.rating} for t in rows]
    return {"ok": True, "items": items, "collection": c.name}


def _actor_refresh_profile(db, args):
    """后台刷新演员资料。"""
    from models import Actor
    aid = _int(args.get("actor_id"))
    a = db.get(Actor, aid)
    if not a:
        return {"ok": False, "message": "演员不存在"}
    try:
        from services.actor_profile import refresh_profile
        import asyncio as _aio
        import threading
        def _run():
            try:
                _aio.run(refresh_profile(aid))
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True, "message": f"已开始刷新 {a.name} 的资料（后台）"}
    except Exception as e:
        return {"ok": False, "message": f"启动失败：{e}"}


def _subscription_update(db, args):
    sid = _int(args.get("subscription_id"))
    sub = db.get(Subscription, sid)
    if not sub:
        return {"ok": False, "message": "订阅不存在"}
    if "auto_add" in args:
        sub.auto_add = bool(args["auto_add"])
    if "enabled" in args:
        sub.enabled = bool(args["enabled"])
    if "check_interval_hours" in args:
        try:
            sub.check_interval_hours = min(max(int(args["check_interval_hours"]), 1), 168)
        except (TypeError, ValueError):
            pass
    if "name" in args:
        sub.name = str(args["name"])[:100]
    db.commit()
    return {"ok": True, "message": f"订阅「{sub.name}」已更新（auto_add={sub.auto_add}）"}


def _download_stats(db, args):
    from models import Download
    from sqlalchemy import func
    total = db.execute(select(func.count(Download.id))).scalar() or 0
    ok = db.execute(select(func.count(Download.id)).where(Download.status == "completed")).scalar() or 0
    return {"ok": True, "stats": {"total": total, "completed": ok,
                                  "success_rate": round(ok / total, 3) if total else 0}}


def _drive115_status(db, args):
    try:
        from services.drive115_client import get_quota, list_offline_tasks
        import asyncio as _aio
        async def _g():
            q = await get_quota()
            tasks = await list_offline_tasks()
            return q, tasks
        q, tasks = _aio.run(_g())
        return {"ok": True, "quota": q, "offline_tasks": len(tasks) if tasks else 0}
    except Exception as e:
        return {"ok": False, "message": f"115 状态获取失败：{e}"}


def _media_check(db, args):
    from routers.media_server import check_in_library
    vc = str(args.get("video_code") or "").strip().upper()
    if not vc:
        return {"ok": False, "message": "需要番号"}
    try:
        r = check_in_library(vc, db, "anonymous")
        return {"ok": True, "video_code": vc, "result": r}
    except Exception as e:
        return {"ok": False, "message": f"查询失败：{e}"}


def _media_sync(db, args):
    """后台触发媒体服务器同步。"""
    try:
        from routers.media_server import sync
        import asyncio as _aio
        import threading
        force = bool(args.get("force"))
        def _run():
            try:
                _aio.run(sync("anonymous", limit=200, force=force))
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True, "message": "媒体服务器同步已开始（后台）"}
    except Exception as e:
        return {"ok": False, "message": f"启动失败：{e}"}


def _organize_config(db, args):
    from routers.organize import get_config
    try:
        r = get_config(db, "anonymous")
        return {"ok": True, "config": r if isinstance(r, dict) else {}}
    except Exception as e:
        return {"ok": False, "message": f"读取失败：{e}"}


def _organize_run(db, args):
    try:
        from routers.organize import run_all
        import asyncio as _aio
        import threading
        def _run():
            try:
                _aio.run(run_all("anonymous"))
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True, "message": "整理任务已开始（后台）"}
    except Exception as e:
        return {"ok": False, "message": f"启动失败：{e}"}


def _organize_undo(db, args):
    from routers.organize import undo
    try:
        r = undo(int(args.get("dl_id") or 0), "anonymous")
        msg = r.get("message") if isinstance(r, dict) else str(r)
        return {"ok": True, "message": f"已解除整理：{msg}"}
    except Exception as e:
        return {"ok": False, "message": f"操作失败：{e}"}


def _system_status(db, args):
    from routers.system import system_status
    try:
        r = system_status(db, "anonymous")
        return {"ok": True, "status": r}
    except Exception as e:
        return {"ok": False, "message": f"读取失败：{e}"}


def _backup_now(db, args):
    from routers.settings import backup_settings
    try:
        r = backup_settings(db, "anonymous")
        return {"ok": True, "message": f"设置已备份（{len(r.get('settings', {}))} 项）"}
    except Exception as e:
        return {"ok": False, "message": f"备份失败：{e}"}


def _dnd_set(db, args):
    from models import Setting
    start = str(args.get("start") or "").strip()[:5]
    end = str(args.get("end") or "").strip()[:5]
    for key, val in (("notify_dnd_start", start), ("notify_dnd_end", end)):
        row = db.get(Setting, key)
        if row:
            row.value = val
        else:
            db.add(Setting(key=key, value=val))
    db.commit()
    return {"ok": True, "message": f"免打扰时段已设置：{start or '--'}~{end or '--'}"}


def _notify_test(db, args):
    try:
        from routers.notifications import test_notify
        import asyncio as _aio
        async def _run():
            return await test_notify("anonymous")
        r = _aio.run(_run())
        return {"ok": True, "message": f"测试通知已发送：{r}"}
    except Exception as e:
        return {"ok": False, "message": f"发送失败：{e}"}


def _list_source_list(db, args):
    from models import ListSource
    rows = db.execute(select(ListSource).order_by(ListSource.id)).scalars().all()
    items = [{"id": r.id, "list_code": r.list_code, "list_path": r.list_path,
              "max_pages": r.max_pages} for r in rows]
    return {"ok": True, "items": items}


def _list_source_add(db, args):
    from models import ListSource
    code = str(args.get("list_code") or "").strip()[:50]
    path = str(args.get("list_path") or "").strip()[:200]
    if not code or not path:
        return {"ok": False, "message": "需要 list_code 与 list_path"}
    ls = ListSource(list_code=code, list_path=path,
                    list_params=str(args.get("list_params") or "")[:100] or "f=download",
                    max_pages=min(int(args.get("max_pages") or 100), 1000))
    db.add(ls)
    db.commit()
    db.refresh(ls)
    return {"ok": True, "message": f"列表源 {code} 已添加", "id": ls.id}


def _list_source_delete(db, args):
    from models import ListSource
    lid = _int(args.get("source_id"))
    ls = db.get(ListSource, lid)
    if not ls:
        return {"ok": False, "message": "列表源不存在"}
    db.delete(ls)
    db.commit()
    return {"ok": True, "message": f"列表源 {ls.list_code} 已删除"}


def _yearly_report(db, args):
    from routers.insights import yearly_report
    try:
        y = int(args.get("year") or 0) or None
        r = yearly_report(db, "anonymous", year=y)
        return {"ok": True, "report": r}
    except Exception as e:
        return {"ok": False, "message": f"生成失败：{e}"}


def _wishlist_gaps(db, args):
    from routers.tasks import wishlist_gaps
    try:
        r = wishlist_gaps(db, "anonymous", limit=50)
        items = r.get("items") if isinstance(r, dict) else []
        out = [{"task_id": it.get("task_id"), "video_code": it.get("video_code"), "title": it.get("title")} for it in items[:20]]
        return {"ok": True, "items": out}
    except Exception as e:
        return {"ok": False, "message": f"查询失败：{e}"}


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
        esc = str(tk).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Task.title.like(f"%{esc}%", escape="\\"))
    rm = query.get("rating_min")
    if rm is not None:
        try:
            stmt = stmt.where(Task.rating >= float(rm))
        except Exception:
            pass
    for t in (query.get("tags") or [])[:5]:
        if t:
            try:
                from sqlalchemy import or_
                from services.tag_translate import cn_aliases
                conds = [Task.tags.like(f"%{str(t).replace(chr(92), chr(92)*2).replace('%', chr(92)+'%').replace('_', chr(92)+'_')}%", escape=chr(92))]
                for a in cn_aliases(str(t))[1:]:
                    ea = str(a).replace(chr(92), chr(92)*2).replace('%', chr(92)+'%').replace('_', chr(92)+'_')
                    conds.append(Task.tags.like(f"%{ea}%", escape=chr(92)))
                stmt = stmt.where(or_(*conds) if len(conds) > 1 else conds[0])
            except Exception as e:
                logger.warning("标签别名匹配失败: %s", e)
    for a in (query.get("actors") or [])[:5]:
        if a:
            esc_a = str(a).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            stmt = stmt.where(Task.actors.like(f"%{esc_a}%", escape="\\"))
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
        if tool_name in ("batch_push", "batch_delete", "ranking_add_tasks"):
            ids = [int(x) for x in (args.get("task_ids") or args.get("ranking_ids") or []) if str(x).isdigit()]
            return f"将影响 {len(ids)} 条记录；确认后执行"
        if tool_name == "task_delete":
            t = db.get(Task, int(args.get("task_id") or 0))
            return f"将删除作品 {t.video_code if t else args.get('task_id')}；确认后执行"
        if tool_name == "push_download":
            t = db.get(Task, int(args.get("task_id") or 0))
            return f"将推送 {t.video_code if t else args.get('task_id')} 给下载器；确认后执行"
        if tool_name == "task_dedupe":
            dry = bool(args.get("dry_run", True))
            return "去重将执行（dry_run=false，删除重复任务）；建议先预览再确认" if not dry else "去重预览（dry_run=true）"
        if tool_name == "actor_blacklist":
            from models import Actor
            a = db.get(Actor, int(args.get("actor_id") or 0))
            return f"将{'拉黑' if a and not getattr(a, 'blacklisted', False) else '取消拉黑'}演员 {a.name if a else args.get('actor_id')}；确认后执行"
        if tool_name == "crawl_control":
            return f"将{ {'pause': '暂停', 'resume': '恢复', 'stop': '停止'}.get(str(args.get('action')), '控制') }爬虫；确认后执行"
        if tool_name == "tag_translate_apply":
            mp = args.get("mapping") or {}
            if isinstance(mp, dict) and mp:
                head = "、".join(f"{k}→{v}" for k, v in list(mp.items())[:5])
                return f"将把 {len(mp)} 个英文标签替换为中文（前 5：{head}）；确认后执行"
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
     "desc": "创建订阅（类型：actor=演员新作 / ranking=排行榜 / composite=综合条件）",
     "args": {"sub_type": "actor|ranking|composite", "name": "订阅名称",
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
    {"name": "pref_get", "cn": "查看偏好", "is_write": False,
     "desc": "查看检索偏好（默认评分/标签/只看未看/排序）", "args": {}, "handler": _pref_get},
    {"name": "pref_set", "cn": "设置偏好", "is_write": True,
     "desc": "设置检索偏好（如：以后默认只看 8 分以上）",
     "args": {"key": "rating_min|tags|only_unviewed|sort", "value": "新值"},
     "handler": _pref_set},
    {"name": "pref_clear", "cn": "清除偏好", "is_write": True,
     "desc": "清除全部检索偏好", "args": {}, "handler": _pref_clear},
    {"name": "tag_translate", "cn": "标签翻译", "is_write": False,
     "desc": "把库内英文标签翻译为中文（返回映射预览，确认后应用）",
     "args": {}, "handler": _tag_translate},
    {"name": "tag_translate_apply", "cn": "应用标签翻译", "is_write": True,
     "desc": "应用标签中英映射（把英文标签替换为中文）",
     "args": {"mapping": "英文→中文映射对象"}, "handler": _tag_translate_apply},
    {"name": "fill_works", "cn": "全部补齐作品", "is_write": True,
     "desc": "启动全部补齐作品后台任务（爬取所有订阅演员的作品）",
     "args": {"wait_limit_min": "等待上限分钟（可选）"}, "handler": _fill_works},
    {"name": "actor_crawl_works", "cn": "爬取演员作品", "is_write": True,
     "desc": "爬取指定演员的全部作品（后台进行）",
     "args": {"actor_id": "演员 ID"}, "handler": _actor_crawl_works},
    {"name": "collection_list", "cn": "收藏夹列表", "is_write": False,
     "desc": "查看收藏夹列表", "args": {}, "handler": _collection_list},
    {"name": "collection_create", "cn": "创建收藏夹", "is_write": True,
     "desc": "创建收藏夹", "args": {"name": "收藏夹名称", "description": "描述（可选）"},
     "handler": _collection_create},
    {"name": "collection_add", "cn": "加入收藏夹", "is_write": True,
     "desc": "把作品加入收藏夹", "args": {"collection_id": "收藏夹 ID", "task_id": "作品 ID"},
     "handler": _collection_add},
    {"name": "download_list", "cn": "下载列表", "is_write": False,
     "desc": "查看最近下载任务", "args": {}, "handler": _download_list},
    {"name": "notify_list", "cn": "通知记录", "is_write": False,
     "desc": "查看最近通知记录", "args": {}, "handler": _notify_list},
    {"name": "batch_retry", "cn": "批量重试", "is_write": True,
     "desc": "重新排队失败任务（最多 200 个）",
     "args": {"limit": "数量（可选）"}, "handler": _batch_retry},
    {"name": "magnet_search", "cn": "磁力搜索", "is_write": False,
     "desc": "多源搜索番号的磁力资源", "args": {"video_code": "番号"}, "handler": _magnet_search},
    {"name": "push_download", "cn": "推送下载", "is_write": True,
     "desc": "把作品磁力推送给下载器（后台）",
     "args": {"task_id": "作品 ID"}, "handler": _push_download},
    {"name": "batch_push", "cn": "批量推送", "is_write": True,
     "desc": "批量推送下载（最多 50 部）",
     "args": {"task_ids": "作品 ID 数组"}, "handler": _batch_push},
    {"name": "new_releases_list", "cn": "新作列表", "is_write": False,
     "desc": "查看最近新作（可按演员/只看未读）",
     "args": {"actor_id": "演员 ID（可选）", "only_unread": "只看未读（可选）"},
     "handler": _new_releases_list},
    {"name": "new_release_add", "cn": "新作入库", "is_write": True,
     "desc": "把新作记录加入库", "args": {"new_release_id": "新作 ID"}, "handler": _new_release_add},
    {"name": "new_release_read", "cn": "新作已读", "is_write": True,
     "desc": "标记新作为已读", "args": {"new_release_id": "新作 ID"}, "handler": _new_release_read},
    {"name": "new_release_check_all", "cn": "全量检查新作", "is_write": True,
     "desc": "检查所有订阅演员的新作（后台）", "args": {}, "handler": _new_release_check_all},
    {"name": "ranking_list", "cn": "排行榜", "is_write": False,
     "desc": "查看排行榜（daily/weekly/monthly）",
     "args": {"rank_type": "daily|weekly|monthly", "date": "日期（可选，默认最新）"},
     "handler": _ranking_list},
    {"name": "ranking_add_tasks", "cn": "榜单入库", "is_write": True,
     "desc": "把榜单条目加入库为待处理任务",
     "args": {"ranking_ids": "榜单条目 ID 数组"}, "handler": _ranking_add_tasks},
    {"name": "task_delete", "cn": "删除作品", "is_write": True,
     "desc": "删除单个作品任务", "args": {"task_id": "作品 ID"}, "handler": _task_delete},
    {"name": "batch_delete", "cn": "批量删除", "is_write": True,
     "desc": "批量删除作品任务（最多 200 个）",
     "args": {"task_ids": "作品 ID 数组"}, "handler": _batch_delete},
    {"name": "drive115_offline_add", "cn": "115 离线下载", "is_write": True,
     "desc": "把磁力添加到 115 离线下载",
     "args": {"magnet": "磁力链接", "task_id": "作品 ID（可选，二选一）"},
     "handler": _drive115_offline_add},
    {"name": "actor_detail", "cn": "演员详情", "is_write": False,
     "desc": "查看演员档案（简介/生日/身高/三围/作品数）",
     "args": {"actor_id": "演员 ID"}, "handler": _actor_detail},
    {"name": "actor_blacklist", "cn": "拉黑演员", "is_write": True,
     "desc": "切换演员黑名单状态",
     "args": {"actor_id": "演员 ID"}, "handler": _actor_blacklist},
    {"name": "filter_rule_list", "cn": "过滤规则列表", "is_write": False,
     "desc": "查看内容过滤规则（与自动规则不同模块）", "args": {}, "handler": _filter_rule_list},
    {"name": "filter_rule_create", "cn": "创建过滤规则", "is_write": True,
     "desc": "创建内容过滤规则（按关键词过滤标题/演员/标签）",
     "args": {"name": "规则名", "keyword": "关键词", "action": "hide|block|mark|exclude",
              "fields_json": "字段 JSON（可选）", "is_regex": "是否正则（可选）"},
     "handler": _filter_rule_create},
    {"name": "filter_rule_delete", "cn": "删除过滤规则", "is_write": True,
     "desc": "删除内容过滤规则", "args": {"rule_id": "规则 ID"}, "handler": _filter_rule_delete},
    {"name": "task_dedupe", "cn": "批量去重", "is_write": True,
     "desc": "番号归一化去重（先预览再确认执行）",
     "args": {"dry_run": "默认 true 预览；false 执行"}, "handler": _task_dedupe},
    {"name": "crawl_status", "cn": "爬虫状态", "is_write": False,
     "desc": "查看爬虫运行状态", "args": {}, "handler": _crawl_status},
    {"name": "crawl_control", "cn": "爬虫控制", "is_write": True,
     "desc": "暂停/恢复/停止爬虫", "args": {"action": "pause|resume|stop"}, "handler": _crawl_control},
    {"name": "fill_works_status", "cn": "补齐进度", "is_write": False,
     "desc": "查看全部补齐作品任务进度", "args": {}, "handler": _fill_works_status},
    {"name": "recommendations", "cn": "推荐作品", "is_write": False,
     "desc": "按口味偏好推荐作品", "args": {"limit": "数量（可选）"}, "handler": _recommendations},
    {"name": "similar_works", "cn": "相似作品", "is_write": False,
     "desc": "找与指定作品相似的作品",
     "args": {"task_id": "作品 ID", "limit": "数量（可选）"}, "handler": _similar_works},
    {"name": "task_extract", "cn": "重新提取", "is_write": True,
     "desc": "触发作品元数据提取（后台）",
     "args": {"task_id": "作品 ID"}, "handler": _task_extract},
    {"name": "task_note", "cn": "作品备注", "is_write": True,
     "desc": "给作品添加/更新备注", "args": {"task_id": "作品 ID", "note": "备注内容"},
     "handler": _task_note},
    {"name": "task_favorite", "cn": "收藏作品", "is_write": True,
     "desc": "收藏/取消收藏作品（与收藏夹不同）",
     "args": {"task_id": "作品 ID", "favorite": "true/false"}, "handler": _task_favorite},
    {"name": "favorites_list", "cn": "收藏列表", "is_write": False,
     "desc": "查看已收藏作品", "args": {}, "handler": _favorites_list},
    {"name": "collection_delete", "cn": "删除收藏夹", "is_write": True,
     "desc": "删除收藏夹", "args": {"collection_id": "收藏夹 ID"}, "handler": _collection_delete},
    {"name": "collection_remove", "cn": "移出收藏夹", "is_write": True,
     "desc": "把作品移出收藏夹",
     "args": {"collection_id": "收藏夹 ID", "task_id": "作品 ID"}, "handler": _collection_remove},
    {"name": "collection_tasks", "cn": "收藏夹内容", "is_write": False,
     "desc": "查看收藏夹内的作品", "args": {"collection_id": "收藏夹 ID"}, "handler": _collection_tasks},
    {"name": "actor_refresh_profile", "cn": "刷新演员资料", "is_write": True,
     "desc": "后台刷新演员资料", "args": {"actor_id": "演员 ID"}, "handler": _actor_refresh_profile},
    {"name": "subscription_update", "cn": "更新订阅", "is_write": True,
     "desc": "更新订阅（auto_add/启用/间隔/名称）",
     "args": {"subscription_id": "订阅 ID", "auto_add": "可选", "enabled": "可选",
              "check_interval_hours": "可选", "name": "可选"},
     "handler": _subscription_update},
    {"name": "download_stats", "cn": "下载统计", "is_write": False,
     "desc": "下载完成率统计", "args": {}, "handler": _download_stats},
    {"name": "drive115_status", "cn": "115 状态", "is_write": False,
     "desc": "115 配额与离线任务状态", "args": {}, "handler": _drive115_status},
    {"name": "media_check", "cn": "媒体库检查", "is_write": False,
     "desc": "检查作品是否在媒体服务器（Emby）",
     "args": {"video_code": "番号"}, "handler": _media_check},
    {"name": "media_sync", "cn": "媒体库同步", "is_write": True,
     "desc": "触发媒体服务器同步（后台）",
     "args": {"force": "全量同步（可选）"}, "handler": _media_sync},
    {"name": "organize_config", "cn": "整理配置", "is_write": False,
     "desc": "查看文件整理配置", "args": {}, "handler": _organize_config},
    {"name": "organize_run", "cn": "运行整理", "is_write": True,
     "desc": "手动触发全量整理（后台）", "args": {}, "handler": _organize_run},
    {"name": "organize_undo", "cn": "解除整理", "is_write": True,
     "desc": "解除指定下载的整理", "args": {"dl_id": "下载 ID"}, "handler": _organize_undo},
    {"name": "system_status", "cn": "系统状态", "is_write": False,
     "desc": "系统状态全景（调度/队列/活跃下载/最近错误/备份）",
     "args": {}, "handler": _system_status},
    {"name": "backup_now", "cn": "立即备份", "is_write": True,
     "desc": "导出全部设置为备份", "args": {}, "handler": _backup_now},
    {"name": "dnd_set", "cn": "免打扰设置", "is_write": True,
     "desc": "设置通知免打扰时段（HH:MM，空值清除）",
     "args": {"start": "开始（可选）", "end": "结束（可选）"}, "handler": _dnd_set},
    {"name": "notify_test", "cn": "测试通知", "is_write": True,
     "desc": "发送测试通知验证通道", "args": {}, "handler": _notify_test},
    {"name": "list_source_list", "cn": "列表源列表", "is_write": False,
     "desc": "查看列表源", "args": {}, "handler": _list_source_list},
    {"name": "list_source_add", "cn": "添加列表源", "is_write": True,
     "desc": "添加列表源", "args": {"list_code": "代码", "list_path": "路径",
              "list_params": "参数（可选）", "max_pages": "最大页数（可选）"},
     "handler": _list_source_add},
    {"name": "list_source_delete", "cn": "删除列表源", "is_write": True,
     "desc": "删除列表源", "args": {"source_id": "列表源 ID"}, "handler": _list_source_delete},
    {"name": "yearly_report", "cn": "年度报告", "is_write": False,
     "desc": "查看年度回顾报告", "args": {"year": "年份（可选）"}, "handler": _yearly_report},
    {"name": "wishlist_gaps", "cn": "想看缺口", "is_write": False,
     "desc": "想看但无磁力/不在库的作品", "args": {}, "handler": _wishlist_gaps},
]

_TOOL_PROMPT = "\n".join(
    f"- {t['name']}（{t['cn']}，{'写操作需确认' if t['is_write'] else '只读'}）：{t['desc']}；参数：{json.dumps(t['args'], ensure_ascii=False)}"
    for t in TOOLS
)


def _int(v, default: int = 0) -> int:
    """安全整型转换（LLM 输出参数不可信）。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


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
    if a.operator not in ("ai", op):
        return {"ok": False, "message": "无权撤销他人发起的操作"}
    if a.tool == "set_view_status":
        # 还原旧观看状态（记录旧值）
        old_status = args.get("_old_view_status")
        tid = _int(args.get("task_id"))
        t = db.get(Task, tid)
        if not t:
            return {"ok": False, "message": "作品不存在，无法撤销"}
        t.view_status = old_status or None
        db.commit()
        msg = f"已还原作品 #{tid} 观看状态"
    elif a.tool == "subscription_toggle":
        # 反转开关
        sid = _int(args.get("subscription_id"))
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
            query = _apply_prefs(query, _get_prefs(db, user))
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
    if tool_name == "magnet_search":
        items = result.get("items", [])
        lines = [f"- [{it['source']}] {it.get('size') or '?'} {it.get('title', '')[:30]}" for it in items[:8]]
        return f"找到 {result.get('total', len(items))} 个磁力：\n" + "\n".join(lines)
    if tool_name == "new_releases_list":
        items = result.get("items", [])
        if not items:
            return "暂无新作"
        return "\n".join(f"- #{r['id']} {r['video_code']} {r.get('title') or ''}（{r['release_date'] or '?'}{'，未读' if not r['is_read'] else ''}）" for r in items[:15])
    if tool_name == "ranking_list":
        items = result.get("items", [])
        if not items:
            return result.get("message") or "暂无榜单数据"
        return f"榜单 {result.get('rank_type')} {result.get('date')}：\n" + "\n".join(
            f"- {r['rank']}. {r['video_code']}{' ' + str(r['rating']) if r.get('rating') else ''} {r.get('title') or ''}" for r in items[:20])
    if tool_name == "actor_detail":
        a = result["actor"]
        return (f"演员：{a['name']}（ID {a['id']}）\n"
                f"生日：{a.get('birth_date') or '-'} | 身高：{a.get('height') or '-'} | 罩杯：{a.get('cup') or '-'} | 三围：{a.get('measurements') or '-'}\n"
                f"作品数：{a.get('works_count')} | 关注：{'是' if a.get('is_followed') else '否'}\n"
                f"简介：{(a.get('intro') or '-')[:100]}")
    if tool_name in ("filter_rule_list",):
        items = result.get("items", [])
        if not items:
            return "暂无内容过滤规则"
        return "\n".join(f"- #{r['id']} {r['name']}：{r['keyword']}（{r['action']}，{'启用' if r['enabled'] else '停用'}）" for r in items)
    if tool_name in ("recommendations", "similar_works"):
        items = result.get("items", [])
        if not items:
            return "暂无推荐"
        label = "推荐" if tool_name == "recommendations" else "相似作品"
        return f"{label}：\n" + "\n".join(
            f"- {it['video_code']}{' ' + str(it['rating']) if it.get('rating') else ''} {it.get('title') or ''}" for it in items[:8])
    if tool_name == "fill_works_status":
        st = result.get("status", {})
        return f"补齐进度：{st}"
    if tool_name == "crawl_status":
        return f"爬虫{'运行中' if result.get('running') else '空闲'}" + (f"（{result.get('owner')}）" if result.get('owner') else "")
    if tool_name in ("favorites_list", "collection_tasks", "wishlist_gaps"):
        items = result.get("items", [])
        if not items:
            return "暂无数据"
        label = {"favorites_list": "收藏", "collection_tasks": f"收藏夹「{result.get('collection', '')}」",
                 "wishlist_gaps": "想看缺口"}[tool_name]
        return f"{label}：\n" + "\n".join(
            f"- {it['video_code']}{' ' + str(it['rating']) if it.get('rating') else ''} {it.get('title') or ''}" for it in items[:15])
    if tool_name == "list_source_list":
        items = result.get("items", [])
        if not items:
            return "暂无列表源"
        return "\n".join(f"- #{r['id']} {r['list_code']}（{r['list_path']}）" for r in items)
    if tool_name == "download_stats":
        st = result.get("stats", {})
        return f"下载统计：共 {st.get('total', 0)} 个，完成 {st.get('completed', 0)} 个，成功率 {st.get('success_rate', 0) * 100:.0f}%"
    if tool_name == "system_status":
        st = result.get("status", {})
        return f"系统状态：{st}"
    if tool_name == "tag_translate":
        mp = result.get("mapping") or {}
        if not mp:
            return result.get("message") or "无待翻译标签"
        lines = [f"- {k} → {v}" for k, v in list(mp.items())[:15]]
        more = f"…共 {len(mp)} 个映射" if len(mp) > 15 else ""
        return "标签翻译映射（说「应用标签翻译」确认执行）：\n" + "\n".join(lines) + (f"\n{more}" if more else "")
    if tool_name in ("collection_list", "download_list", "notify_list"):
        items = result.get("items", [])
        if not items:
            return "暂无数据"
        if tool_name == "collection_list":
            return "\n".join(f"- #{c['id']} {c['name']}（{c['count']} 部）" for c in items)
        if tool_name == "download_list":
            return "\n".join(f"- {d['video_code'] or d['id']}：{d['status']}{(' ' + str(d['progress']) + '%') if d.get('progress') is not None else ''}（{d['downloader']}）" for d in items)
        return "\n".join(f"- {n['title'] or n['event']}（{'✓' if n['ok'] else '✗'} {n['created_at']}）" for n in items)
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


# ---------- 斜杠命令路由（跳过 LLM，直达工具） ----------
COMMANDS = {
    "help": {"cn": "帮助", "tool": None, "desc": "显示全部命令"},
    "stats": {"cn": "库统计", "tool": "stats", "args": {}},
    "inspect": {"cn": "系统巡检", "tool": "inspect", "args": {}},
    "health": {"cn": "库健康建议", "tool": "health_advice", "args": {}},
    "subs": {"cn": "订阅列表", "tool": "subscription_list", "args": {}},
    "rules": {"cn": "规则列表", "tool": "rule_list", "args": {}},
    "prefs": {"cn": "查看偏好", "tool": "pref_get", "args": {}},
    "recommend": {"cn": "推荐新片", "tool": "search", "args": {"question": "高分未看过的作品"}},
    "weekly": {"cn": "本周新作", "tool": "search", "args": {"question": "最近发布的高分作品"}},
}


async def run_command(cmd: str, arg_text: str, db, user) -> dict:
    """执行斜杠命令（命令体参数走 NL 解析兜底）。"""
    cmd = cmd.lower().strip().lstrip("/")
    if cmd in ("help", "?"):
        lines = ["可用命令："]
        for k, v in COMMANDS.items():
            lines.append(f"- /{k}：{v['cn']}")
        lines.append("- /sub <条件>：创建订阅（如 /sub 8分以上巨乳）")
        lines.append("- /mark <条件> <看过|想看>：批量标记（如 /mark 8分以上 想看）")
        lines.append("- /combo <条件>：标记+建订阅一次完成")
        return {"ok": True, "type": "answer", "content": "\n".join(lines), "steps": []}

    if cmd == "sub":
        if not arg_text:
            return {"ok": True, "type": "error", "content": "用法：/sub <条件>，如 /sub 8分以上巨乳"}
        t = _pick_tool("subscription_create")
        args = {"sub_type": "composite", "name": f"命令订阅（{arg_text[:12]}）", "filters_json": None}
        # 解析条件 → filters
        try:
            q, _e = await _parse_question(arg_text)
            filters = {k: v for k, v in q.items() if k in ("makers", "labels", "series", "exclude_codes", "min_rating", "date_from") and v}
            if q.get("tags"):
                filters["genres"] = q["tags"]
            args["filters_json"] = json.dumps(filters, ensure_ascii=False) if filters else None
        except Exception:
            pass
        return await _execute_step(t, "subscription_create", args, f"命令 /sub {arg_text}", db, user, True)

    if cmd == "mark":
        # /mark <条件> <状态>；状态关键词解析
        status = "want"
        for k in ("看过", "想看", "未看"):
            if k in arg_text:
                status = {"看过": "viewed", "想看": "want", "未看": "none"}[k]
                arg_text = arg_text.replace(k, "").strip()
                break
        t = _pick_tool("batch_set_view_status")
        args = {"query_text": arg_text or "高分作品", "view_status": status}
        try:
            q, _e = await _parse_question(arg_text or "高分作品")
            args["_query"] = q
        except Exception:
            pass
        return await _execute_step(t, "batch_set_view_status", args, f"命令 /mark", db, user, True)

    if cmd == "combo":
        t = _pick_tool("combo_mark_subscribe")
        args = {"query_text": arg_text or "高分作品", "view_status": "want"}
        try:
            q, _e = await _parse_question(arg_text or "高分作品")
            args["_query"] = q
        except Exception:
            pass
        return await _execute_step(t, "combo_mark_subscribe", args, f"命令 /combo", db, user, True)

    c = COMMANDS.get(cmd)
    if not c or not c["tool"]:
        return {"ok": True, "type": "answer",
                "content": f"未知命令 /{cmd}。输入 /help 查看全部命令。", "steps": []}
    t = _pick_tool(c["tool"])
    if not t:
        return {"ok": True, "type": "error", "content": f"命令 /{cmd} 暂不可用", "steps": []}
    args = dict(c["args"])
    if arg_text and c["tool"] == "search":
        args["question"] = arg_text
    return await _execute_step(t, c["tool"], args, f"命令 /{cmd}", db, user, True)


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
