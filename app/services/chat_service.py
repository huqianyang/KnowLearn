"""问答服务 C1（API 设计第 5 章）：流式（SSE）与非流式两种方式共用一条执行路径。

执行模型：问答图在工作线程中运行，通过 emit 把事件放进 asyncio 队列；
SSE 生成器从队列取事件推给浏览器。消息落库、释放会话锁都在工作线程的 finally 中完成，
因此客户端中途断开（停止生成、刷新页面）时也会按 interrupted 状态保存已生成的部分。
"""
import asyncio
import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone

from openai import APIConnectionError, APIStatusError, APITimeoutError
from pymilvus.exceptions import MilvusException

from app.core.errors import BizError, ErrorCode
from app.pipelines.query.graph import QueryContext, QueryPipeline
from app.services.session_service import SessionService, message_view

logger = logging.getLogger(__name__)

_END = "__end__"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _classify(e: Exception) -> tuple[int, str]:
    if isinstance(e, (APIConnectionError, APITimeoutError, APIStatusError)):
        return ErrorCode.DEPENDENCY_UNAVAILABLE.code, "模型服务暂时不可用，请稍后重试"
    if isinstance(e, MilvusException):
        return ErrorCode.DEPENDENCY_UNAVAILABLE.code, "知识库服务暂时不可用，请稍后重试"
    return ErrorCode.INTERNAL.code, "回答生成失败，请稍后重试"


class ChatService:
    def __init__(self, sessions: SessionService, pipeline: QueryPipeline, history_rounds: int):
        self.sessions = sessions
        self.pipeline = pipeline
        self.history_rounds = history_rounds

    def _start(self, session_id: str, query: str, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> threading.Event:
        """校验并抢占会话锁、保存用户消息，然后在后台线程运行问答图。返回取消信号。"""
        session = self.sessions.acquire(session_id)
        try:
            history = self.sessions.history(session_id, self.history_rounds)
            now = datetime.now(timezone.utc)
            self.sessions.add_message({"_id": uuid.uuid4().hex, "session_id": session_id, "role": "user",
                                       "content": query, "status": "completed", "created_at": now},
                                      first_question=query if session.get("message_count", 0) == 0 else None)
        except Exception:
            self.sessions.release(session_id)
            raise

        buffer: list[str] = []

        def emit(event: str, data: dict) -> None:
            if event == "delta":
                buffer.append(data["text"])
            loop.call_soon_threadsafe(queue.put_nowait, (event, data))

        ctx = QueryContext(emit=emit)
        threading.Thread(target=self._work, args=(session_id, query, history, ctx, buffer, now),
                         name=f"chat-{session_id[:8]}", daemon=True).start()
        return ctx.cancel

    def _work(self, session_id: str, query: str, history: list[dict], ctx: QueryContext,
              buffer: list[str], user_time: datetime) -> None:
        msg_id = uuid.uuid4().hex
        state: dict = {}
        status, error = "completed", None
        try:
            state = self.pipeline.run(query, history, ctx)
            if ctx.cancel.is_set():
                status = "interrupted"
        except Exception as e:  # noqa: BLE001
            code, msg = _classify(e)
            logger.exception("[%s] 问答失败", session_id)
            status, error = ("interrupted", None) if ctx.cancel.is_set() else ("failed", msg)
            if status == "failed":
                ctx.emit("error", {"code": code, "message": msg})
        finally:
            content = state.get("answer") if status == "completed" else "".join(buffer)
            doc = {"_id": msg_id, "session_id": session_id, "role": "assistant", "content": content or "",
                   "rewritten_query": state.get("rewritten_query"), "sources": state.get("sources") or [],
                   "images": state.get("images") or [], "status": status, "error": error,
                   "timings": ctx.timings,
                   # 保证助手消息排在用户消息之后
                   "created_at": max(datetime.now(timezone.utc), user_time + timedelta(milliseconds=1))}
            try:
                self.sessions.add_message(doc)
            except Exception:  # noqa: BLE001
                logger.exception("[%s] 保存助手消息失败", session_id)
            finally:
                self.sessions.release(session_id)
            logger.info("[%s] 问答结束 status=%s timings=%s", session_id, status, ctx.timings)
            if status != "failed":
                ctx.emit("done", {"message_id": msg_id, "content": doc["content"]})
            ctx.emit(_END, {"message": message_view(doc)})

    # ---------------- 流式 ----------------

    async def stream(self, session_id: str, query: str):
        """先同步完成校验和抢锁（失败时以普通 JSON 错误返回），再返回 SSE 事件生成器。"""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        cancel = await asyncio.to_thread(self._start, session_id, query, loop, queue)

        async def gen():
            finished = False
            try:
                while True:
                    event, data = await queue.get()
                    if event == _END:
                        finished = True
                        return
                    yield _sse(event, data)
            finally:
                if not finished:          # 客户端断开（停止生成 / 关闭页面）
                    cancel.set()
                    logger.info("[%s] 客户端断开，停止生成", session_id)
        return gen()

    # ---------------- 非流式 ----------------

    async def complete(self, session_id: str, query: str) -> dict:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        await asyncio.to_thread(self._start, session_id, query, loop, queue)
        while True:
            event, data = await queue.get()
            if event == "error":
                raise BizError(ErrorCode.DEPENDENCY_UNAVAILABLE if data["code"] == 50301 else ErrorCode.INTERNAL,
                               data["message"])
            if event == _END:
                return data["message"]
