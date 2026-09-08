"""迅雷官方 MCP 下载服务客户端（SSE 传输，A 形态试点）。

MCP over SSE：GET 打开事件流（Accept: text/event-stream），JSON-RPC 经 POST 发送；
服务器响应可能经 SSE 事件流或直接 HTTP body 返回，两者皆支持（后者便于测试）。
连接串含应用令牌，存 settings key xunlei_mcp_url（设置面板脱敏显示，不入仓库）。
"""
import asyncio
import json
import logging

import httpx

logger = logging.getLogger("avdb.xunlei_mcp")


class MCPError(Exception):
    pass


class XunleiMCPClient:
    def __init__(self, url: str, timeout: float = 30.0):
        self.url = url
        self._post_url = url  # legacy SSE 用 endpoint 事件指定，默认与 url 同
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._sse_task: asyncio.Task | None = None
        self._sse_ready = asyncio.Event()
        self._endpoint_evt = asyncio.Event()
        self._seq = 0
        self.server_info: dict | None = None
        self.tools: list[dict] | None = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *a):
        await self.close()

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=10))  # S4：不再 follow_redirects
        self._sse_task = asyncio.create_task(self._read_sse())
        try:
            # 等 SSE GET 流建立后再发 initialize（部分服务器会忽略未注册会话的 POST）
            try:
                await asyncio.wait_for(self._sse_ready.wait(), timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                raise MCPError("MCP 事件流未建立（SSE 连接失败）")
            # legacy SSE：等 endpoint 事件告知 POST 地址（最多 1.5s，非 legacy 自动跳过）
            try:
                await asyncio.wait_for(self._endpoint_evt.wait(), timeout=1.5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            res = await self._rpc("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "avdb-server", "version": "1.0"},
            })
            # S5：初始化响应校验（无 protocolVersion 视为协议不兼容）
            if not isinstance(res, dict) or "protocolVersion" not in res:
                raise MCPError("MCP 初始化响应异常（缺少 protocolVersion）")
            self.server_info = res
            await self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        except Exception:
            # S2：连接失败清理（取消 SSE 任务 + 关闭客户端），避免泄漏与挂死
            if self._sse_task:
                self._sse_task.cancel()
                try:
                    await self._sse_task
                except (Exception, asyncio.CancelledError):
                    pass
                self._sse_task = None
            if self._client:
                await self._client.aclose()
                self._client = None
            raise

    async def close(self) -> None:
        if self._sse_task:
            self._sse_task.cancel()
            try:
                await self._sse_task
            except (Exception, asyncio.CancelledError):
                pass
        if self._client:
            await self._client.aclose()
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def _post(self, payload: dict) -> httpx.Response | None:
        r = await self._client.post(self._post_url, json=payload)
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and ct.startswith("application/json"):
            return r
        return None

    async def _rpc(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        self._seq += 1
        rid = self._seq
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            payload = {"jsonrpc": "2.0", "id": rid, "method": method}
            if params is not None:
                payload["params"] = params
            r = await self._post(payload)
            if r is not None:
                body = r.json()
                if "result" in body:
                    return body["result"]
                if "error" in body:
                    raise MCPError(str(body["error"])[:300])
            # S1/S7：SSE 通道收到的是完整信封，统一解包（result 载荷 / error 抛错）
            envelope = await asyncio.wait_for(fut, timeout or self.timeout)
            if not isinstance(envelope, dict):
                return envelope
            if "result" in envelope:
                return envelope["result"]
            if "error" in envelope:
                raise MCPError(str(envelope["error"])[:300])
            return envelope
        finally:
            self._pending.pop(rid, None)

    async def _read_sse(self) -> None:
        """后台读取 SSE 事件流：处理 endpoint 事件 + 按请求 id 分发响应。"""
        try:
            async with self._client.stream(
                    "GET", self.url, headers={"Accept": "text/event-stream"},
                    timeout=httpx.Timeout(None)) as resp:
                self._sse_ready.set()
                event = None
                data_lines: list[str] = []
                async for line in resp.aiter_lines():
                    line = line.rstrip("\r")
                    if line.startswith("event:"):
                        event = line[6:].strip()
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                        continue
                    if line == "":
                        if data_lines:
                            self._handle_sse_event(event, "\n".join(data_lines))
                        event = None
                        data_lines = []
                if data_lines:
                    self._handle_sse_event(event, "\n".join(data_lines))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"迅雷 MCP SSE 流中断: {e}")

    def _handle_sse_event(self, event: str | None, data: str) -> None:
        if not data or not data.strip():
            return
        # legacy SSE：endpoint 事件告知 JSON-RPC 消息的 POST 地址
        if event == "endpoint":
            url = data.strip()
            # 真实服务器下发相对路径（如 /models/message?...），用 SSE 地址 origin 拼接
            if url.startswith("/"):
                from urllib.parse import urljoin
                url = urljoin(self.url.split("?", 1)[0], url)
            if url.startswith(("http://", "https://")):
                logger.info(f"迅雷 MCP endpoint 事件 → POST {url[:96]}")
                self._post_url = url
                self._endpoint_evt.set()
            return
        try:
            msg = json.loads(data)
        except Exception:
            return
        rid = msg.get("id")
        if isinstance(rid, int) and rid in self._pending:
            fut = self._pending[rid]
            if not fut.done():
                if "result" in msg or "error" in msg:
                    fut.set_result(msg)  # 信封原样交付，_rpc 统一解包（S1）

    async def list_tools(self) -> list[dict]:
        res = await self._rpc("tools/list", {})
        self.tools = res.get("tools", []) if isinstance(res, dict) else []
        return self.tools

    async def call_tool(self, name: str, arguments: dict) -> dict:
        res = await self._rpc("tools/call", {"name": name, "arguments": arguments}, timeout=120)
        return res if isinstance(res, dict) else {"result": res}
