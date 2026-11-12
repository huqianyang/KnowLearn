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
    query: str = Field(..., min_length=1, max_length=1000)
    stream: bool = True


@router.post("")
async def create_session(c: Container = Depends(get_container)):
    return ok(await asyncio.to_thread(c.session_service.create))


@router.get("")
async def list_sessions(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100),
                        c: Container = Depends(get_container)):
    return ok(await asyncio.to_thread(c.session_service.list_sessions, page, page_size))


@router.delete("/{session_id}")
async def delete_session(session_id: str, c: Container = Depends(get_container)):
    await asyncio.to_thread(c.session_service.delete, session_id)
    return ok(None, message="已删除")


@router.get("/{session_id}/messages")
async def list_messages(session_id: str, c: Container = Depends(get_container)):
    return ok(await asyncio.to_thread(c.session_service.messages, session_id))


@router.post("/{session_id}/chat")
async def chat(session_id: str, body: ChatRequest, c: Container = Depends(get_container)):
    query = body.query.strip()
    if not query:
        raise BizError(ErrorCode.INVALID_PARAM, "问题不能为空")
    if not body.stream:
        return ok(await c.chat_service.complete(session_id, query))
    gen = await c.chat_service.stream(session_id, query)
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
