"""技术验证 S2：百炼 WebSearch MCP（Streamable HTTP）—— 列出工具、执行一次搜索、查看返回结构。"""
import asyncio
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
URL = os.environ["WEB_SEARCH_MCP_URL"]
HEADERS = {"Authorization": f"Bearer {os.environ['DASHSCOPE_API_KEY']}"}


async def main():
    """连接 MCP 服务，打印工具定义，再用第一个工具搜索一次并打印返回结构。"""
    t = time.time()
    async with streamablehttp_client(URL, headers=HEADERS, timeout=15) as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()
            # 1. 工具名、说明和入参（文档中没有写，靠这里确认）
            tools = (await s.list_tools()).tools
            for tool in tools:
                print("tool:", tool.name)
                print("  description:", (tool.description or "")[:200])
                print("  inputSchema:", json.dumps(tool.inputSchema, ensure_ascii=False))
            # 2. 调用第一个工具；支持 count 参数时一并传入
            name = tools[0].name
            props = tools[0].inputSchema.get("properties", {})
            args = {"query": "Python 函数默认参数 可变对象 陷阱"}
            if "count" in props:
                args["count"] = 5
            t2 = time.time()
            res = await s.call_tool(name, args)
            print(f"call {name}({args}) in {time.time() - t2:.1f}s, isError={res.isError}")
            # 3. 查看返回内容：是否为 JSON、顶层字段、pages 中每条结果的字段
            for c in res.content:
                txt = getattr(c, "text", None)
                if txt is None:
                    print("content type:", c.type); continue
                try:
                    data = json.loads(txt)
                    print("JSON top-level keys:", list(data.keys()) if isinstance(data, dict) else type(data))
                    pages = data.get("pages") if isinstance(data, dict) else None
                    if pages:
                        print("pages count:", len(pages), "; page keys:", list(pages[0].keys()))
                        print("first page:", json.dumps(pages[0], ensure_ascii=False)[:400])
                    else:
                        print(json.dumps(data, ensure_ascii=False)[:800])
                except json.JSONDecodeError:
                    print("text:", txt[:800])
    print(f"total {time.time() - t:.1f}s")

asyncio.run(main())
