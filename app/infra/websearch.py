"""百炼 WebSearch MCP（Streamable HTTP）。

技术验证 S2：工具名 bailian_web_search，参数 query / count；
返回 JSON {pages, request_id, status}，pages[i] 含 title、url、snippet、hostname。
"""
import asyncio
import json
import logging

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

logger = logging.getLogger(__name__)

TOOL_NAME = "bailian_web_search"


class WebSearchClient:
    """联网搜索客户端。每次调用都新建一个 MCP 会话，用完即关。

    Args:
        url: MCP 服务地址。
        api_key: 百炼 API Key。
        timeout: 单次搜索的超时时间（秒），超时后由问答流程降级为只用知识库。
    """

    def __init__(self, url: str, api_key: str, timeout: float = 10.0):
        self.url = url
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.timeout = timeout

    def search(self, query: str, count: int = 5) -> list[dict]:
        """同步调用（在工作线程中使用）。超时或失败时抛出异常，由调用方降级。"""
        # 工作线程中没有事件循环，用 asyncio.run 新建一个
        return asyncio.run(asyncio.wait_for(self._search(query, count), timeout=self.timeout))

    async def _search(self, query: str, count: int) -> list[dict]:
        """调用 MCP 工具并整理结果。

        Returns:
            [{title, url, snippet}]，最多 count 条，没有 url 的结果丢弃。
        """
        # mcp 2.x：请求头需通过自建的 HTTP 客户端传入
        async with create_mcp_http_client(headers=self.headers) as http, \
                streamable_http_client(self.url, http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as s:
                await s.initialize()
                res = await s.call_tool(TOOL_NAME, {"query": query, "count": count})
        if res.is_error:
            raise RuntimeError("联网搜索返回错误")
        # 工具结果是若干段文本，每段是一个 JSON 字符串
        pages: list[dict] = []
        for c in res.content:
            text = getattr(c, "text", None)
            if not text:
                continue
            data = json.loads(text)
            pages += data.get("pages") or []
        # 没有标题时用站点名或网址代替
        return [{"title": p.get("title") or p.get("hostname") or p.get("url", ""),
                 "url": p.get("url", ""), "snippet": (p.get("snippet") or "").strip()}
                for p in pages if p.get("url")][:count]
