# -*- coding: utf-8 -*-
"""鉴权约定测试：白名单外的 GET API 路由在鉴权开启时必须返回 401。
防止"路由漏挂鉴权依赖"（images.py 类遗漏）再次出现。
"""
import re

import pytest

# 无需鉴权的白名单路径（登录/健康检查/登录墙/文档）
WHITELIST = {
    "/api/auth/login",
    "/api/health",
    "/api/health/ready",
    "/api/system/login-wall",
    "/api/system/version",
    "/api/public/share/{token}",  # N21: 公开分享页（免鉴权，token 即凭据）
    "/docs",
    "/openapi.json",
    "/redoc",
}

# 路径参数示例值（用于构造可匹配的请求路径）
PARAM_EXAMPLES = {
    "task_id": "1",
    "actor_id": "1",
    "index": "0",
    "list_source_id": "1",
    "key": "x",
    "rank_type": "hot",
    "task_ids": "1",
}


def _fill_path(path: str) -> str:
    """把 {xxx} 路径参数替换为示例值。"""
    def _repl(m):
        name = m.group(1)
        return PARAM_EXAMPLES.get(name, "1")
    return re.sub(r"\{([^}]+)\}", _repl, path)


def test_api_get_routes_require_auth(client, monkeypatch):
    """鉴权开启时，白名单外的 GET 路由必须返回 401。"""
    from config import get_settings
    from main import app

    # 强制开启鉴权（conftest 默认 AUTH_DISABLED=true，这里覆盖并清缓存）
    monkeypatch.setenv("AUTH_DISABLED", "false")
    get_settings.cache_clear()
    try:
        checked = 0
        skipped = []
        for route in app.routes:
            path = getattr(route, "path", "") or ""
            if not path.startswith("/api/") or path in WHITELIST:
                continue
            methods = getattr(route, "methods", None) or set()
            if "GET" not in methods:
                skipped.append(f"{path} (非 GET，跳过)")
                continue
            url = _fill_path(path)
            resp = client.get(url)
            assert resp.status_code == 401, (
                f"GET {path} 未鉴权返回 {resp.status_code}（应为 401）——路由可能漏挂鉴权依赖"
            )
            checked += 1
        # 至少覆盖了图片/状态/设置等核心 GET 端点
        assert checked >= 5, f"覆盖路由过少（{checked}），白名单或遍历逻辑可能有误"
        print(f"[auth-required] 已校验 {checked} 个 GET 路由均需鉴权；跳过 {len(skipped)} 个非 GET 路由")
    finally:
        get_settings.cache_clear()


def test_images_poster_requires_auth(client, monkeypatch):
    """关键图片端点（曾匿名可读）鉴权开启时必须 401。"""
    from config import get_settings

    monkeypatch.setenv("AUTH_DISABLED", "false")
    get_settings.cache_clear()
    try:
        for url in ("/api/images/poster/1", "/api/images/backdrop/1", "/api/images/avatar/1"):
            resp = client.get(url)
            assert resp.status_code == 401, f"GET {url} 应 401，实际 {resp.status_code}"
    finally:
        get_settings.cache_clear()
