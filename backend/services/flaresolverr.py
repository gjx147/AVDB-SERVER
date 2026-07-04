"""FlareSolverr 兜底代理 —— 当 Playwright stealth 过不了 Cloudflare 时调用。

FlareSolverr 是一个独立的 Docker 服务（sidecar），专门解 Cloudflare challenge。
本模块通过其 HTTP API 提交 URL，返回绕过 challenge 后的 HTML/cookies。

部署：在 docker-compose 里加 flaresolverr 服务，本模块读 FLARESOLVERR_URL 环境变量。
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger("avdb.flaresolverr")

_DEFAULT_URL = os.environ.get("FLARESOLVERR_URL", "").strip()  # 如 http://flaresolverr:8191


async def solve(url: str, *, flaresolverr_url: str | None = None, timeout: float = 60) -> dict | None:
    """提交 URL 给 FlareSolverr 解 Cloudflare challenge。

    返回 {"url": final_url, "status": code, "html": body, "cookies": [...]} 或 None（失败/未配置）。
    """
    base = (flaresolverr_url or _DEFAULT_URL).rstrip("/")
    if not base:
        logger.debug("FlareSolverr 未配置，跳过")
        return None
    payload = {"cmd": "request.get", "url": url, "maxTimeout": int(timeout * 1000)}
    try:
        async with httpx.AsyncClient(timeout=timeout + 10) as client:
            resp = await client.post(f"{base}/v1", json=payload)
            resp.raise_for_status()
            data = resp.json()
        if data.get("status") != "ok":
            logger.warning(f"FlareSolverr 返回非 ok: {data.get('status')} / {data.get('message', '')}")
            return None
        sol = data.get("solution", {})
        return {
            "url": sol.get("url", url),
            "status": sol.get("status"),
            "html": sol.get("response", ""),
            "cookies": sol.get("cookies", []),
            "user_agent": sol.get("userAgent"),
        }
    except Exception as e:
        logger.warning(f"FlareSolverr 调用失败: {e}")
        return None


def is_configured() -> bool:
    """是否配置了 FlareSolverr。"""
    return bool(_DEFAULT_URL)
