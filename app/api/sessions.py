"""会话接口 B1～B4 与问答接口 C1（API 设计第 4、5 章）。"""
import asyncio

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.container import Container, get_container
from app.core.errors import BizError, ErrorCode
from app.core.response import ok

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


class ChatRequest(BaseModel):
    """提问请求体。"""

    query: str = Field(..., min_length=1, max_length=1000)   # 问题，1～1000 字
    stream: bool = True                                      # True 返回 SSE 流；False 等回答完成后一次返回


@router.post("")
async def create_session(c: Container = Depends(get_container)):
    """新建会话，标题默认「新会话」，第一次提问后改为问题的前 20 个字。"""
    return ok(await asyncio.to_thread(c.session_service.create))


@router.get("")
async def list_sessions(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
                        c: Container = Depends(get_container)):
    """会话列表，按最近更新时间倒序。"""
    return ok(await asyncio.to_thread(c.session_service.list_sessions, page, page_size))


@router.delete("/{session_id}")
async def delete_session(session_id: str, c: Container = Depends(get_container)):
    """删除会话及其全部消息。"""
    await asyncio.to_thread(c.session_service.delete, session_id)
    return ok(None, message="已删除")


@router.get("/{session_id}/messages")
async def list_messages(session_id: str, c: Container = Depends(get_container)):
    """会话的全部消息，按时间正序，用于切换会话时还原对话。"""
    return ok(await asyncio.to_thread(c.session_service.messages, session_id))


@router.post("/{session_id}/chat")
async def chat(session_id: str, body: ChatRequest, c: Container = Depends(get_container)):
    """提问。

    - stream=True：返回 SSE，事件顺序为 progress → sources → delta… → done（出错时为 error）；
    - stream=False：等回答生成完毕，返回完整的助手消息。
    同一会话上一个问题还没回答完时返回 40901。
    """
    # Field 的 min_length 不能拦住全空格的问题，这里去掉首尾空白后再判断一次
    query = body.query.strip()
    if not query:
        raise BizError(ErrorCode.INVALID_PARAM, "问题不能为空")
    if not body.stream:
        return ok(await c.chat_service.complete(session_id, query))
    gen = await c.chat_service.stream(session_id, query)
    # 关闭缓存和反向代理缓冲，保证事件能立即推送到浏览器
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
