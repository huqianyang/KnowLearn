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

_END = "__end__"   # 内部结束标记：工作线程全部处理完毕（不推送给浏览器）


def _sse(event: str, data: dict) -> str:
    """按 SSE 格式拼一条事件：event 行 + data 行 + 空行。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _classify(e: Exception) -> tuple[int, str]:
    """把异常归类为对用户友好的错误码和提示（不暴露内部细节）。"""
    if isinstance(e, (APIConnectionError, APITimeoutError, APIStatusError)):
        return ErrorCode.DEPENDENCY_UNAVAILABLE.code, "模型服务暂时不可用，请稍后重试"
    if isinstance(e, MilvusException):
        return ErrorCode.DEPENDENCY_UNAVAILABLE.code, "知识库服务暂时不可用，请稍后重试"
    return ErrorCode.INTERNAL.code, "回答生成失败，请稍后重试"


class ChatService:
    """问答服务。

    Args:
        sessions: 会话服务（锁、历史、消息存取）。
        pipeline: 问答图。
        history_rounds: 带入的历史轮数。
    """

    def __init__(self, sessions: SessionService, pipeline: QueryPipeline, history_rounds: int):
        self.sessions = sessions
        self.pipeline = pipeline
        self.history_rounds = history_rounds

    def _start(self, session_id: str, query: str, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> threading.Event:
        """校验并抢占会话锁、保存用户消息，然后在后台线程运行问答图。返回取消信号。"""
        session = self.sessions.acquire(session_id)
        try:
            # 先取历史再保存本轮问题，历史中不包含本轮
            history = self.sessions.history(session_id, self.history_rounds)
            now = datetime.now(timezone.utc)
            # 会话的第一个问题同时作为会话标题
            self.sessions.add_message({"_id": uuid.uuid4().hex, "session_id": session_id, "role": "user",
                                       "content": query, "status": "completed", "created_at": now},
                                      first_question=query if session.get("message_count", 0) == 0 else None)
        except Exception:
            self.sessions.release(session_id)
            raise

        buffer: list[str] = []   # 已生成的文本，中断时用它保存部分答案

        def emit(event: str, data: dict) -> None:
            """在工作线程中调用：把事件安全地放进事件循环所在线程的队列。"""
            if event == "delta":
                buffer.append(data["text"])
            loop.call_soon_threadsafe(queue.put_nowait, (event, data))

        ctx = QueryContext(emit=emit)
        threading.Thread(target=self._work, args=(session_id, query, history, ctx, buffer, now),
                         name=f"chat-{session_id[:8]}", daemon=True).start()
        return ctx.cancel

    def _work(self, session_id: str, query: str, history: list[dict], ctx: QueryContext,
              buffer: list[str], user_time: datetime) -> None:
        """工作线程主体：运行问答图，结束后无论成败都保存助手消息并释放会话锁。

        消息状态：completed（正常完成）/ interrupted（用户停止）/ failed（出错）。
        """
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
            # 用户已经停止时出现的异常不算失败
            status, error = ("interrupted", None) if ctx.cancel.is_set() else ("failed", msg)
            if status == "failed":
                ctx.emit("error", {"code": code, "message": msg})
        finally:
            # 正常完成用最终答案；中断或失败时用已推送的部分文本
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
            # 结束标记携带完整消息，供非流式接口直接返回
            ctx.emit(_END, {"message": message_view(doc)})

    # ---------------- 流式 ----------------

    async def stream(self, session_id: str, query: str):
        """先同步完成校验和抢锁（失败时以普通 JSON 错误返回），再返回 SSE 事件生成器。"""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        cancel = await asyncio.to_thread(self._start, session_id, query, loop, queue)

        async def gen():
            """从队列取事件并输出 SSE 文本，直到收到结束标记。"""
            finished = False
            try:
                while True:
                    event, data = await queue.get()
                    if event == _END:
                        finished = True
                        return
                    yield _sse(event, data)
            finally:
                # 没收到结束标记就退出，说明浏览器断开了连接（框架会关闭生成器）
                if not finished:          # 客户端断开（停止生成 / 关闭页面）
                    cancel.set()
                    logger.info("[%s] 客户端断开，停止生成", session_id)
        return gen()

    # ---------------- 非流式 ----------------

    async def complete(self, session_id: str, query: str) -> dict:
        """非流式：等待结束标记，返回完整的助手消息；出错时抛出业务异常。"""
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
