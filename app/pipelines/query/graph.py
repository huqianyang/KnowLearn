"""问答图（技术方案 5.1）：

load_history → rewrite ─┬─ kb_search ──┐
                         ├─ hyde_search ┼─ rrf_merge → rerank → generate → END
                         └─ web_search ─┘

三路检索在同一个超步中并行执行。进度、来源、文本增量通过 ctx.emit 推送给服务层（SSE）；
保存消息由服务层在 finally 中完成，以保证失败、中断时也能落库。
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from app.infra.llm import LlmClient
from app.infra.local_models import LocalModels
from app.infra.vector_store import VectorStore
from app.infra.websearch import WebSearchClient
from app.pipelines.query import ranking
from app.prompts.query import ANSWER_SYSTEM, ANSWER_USER, HYDE_SYSTEM, NO_CONTEXT, REWRITE_SYSTEM

logger = logging.getLogger(__name__)

KB_LIMIT = 10              # 知识库混合检索召回条数
HYDE_LIMIT = 10            # HyDE 检索召回条数
WEB_COUNT = 5              # 联网搜索结果条数
HISTORY_CHARS = 1500       # 每条历史消息带入提示词的最大长度


@dataclass
class QueryContext:
    """一次问答的运行上下文，由服务层创建并传入。"""

    emit: Callable[[str, dict], None]                                   # 推送事件：(事件名, 数据)
    cancel: threading.Event = field(default_factory=threading.Event)   # 置位后停止生成（用户点「停止」或断开连接）
    timings: dict[str, float] = field(default_factory=dict)            # 各节点耗时（秒），随助手消息保存


class QueryState(TypedDict, total=False):
    """问答图在节点之间传递的状态。"""

    query: str
    history: list[dict]          # [{"role": "user"|"assistant", "content": str}]，最近 N 轮
    rewritten_query: str         # 改写后的检索问题
    content_types: list[str]     # 识别出的内容类型（空 = 不限）
    question_types: list[str]    # 识别出的题型（空 = 不限）
    kb_hits: list[dict]          # 知识库混合检索结果
    hyde_hits: list[dict]        # HyDE 检索结果
    web_hits: list[dict]         # 联网搜索结果
    web_failed: bool
    merged: list[dict]           # RRF 融合后的候选
    selected: list[dict]         # 重排、截断后用于作答的资料
    sources: list[dict]          # 来源卡片数据
    images: list[dict]           # 回答中展示的图片
    answer: str


class QueryPipeline:
    """问答流程编排。

    Args:
        models: 本机向量化 / 重排模型。
        vectors: Milvus。
        llm: 大模型客户端。
        web: 联网搜索客户端。
        rerank_min_score: 重排相关性阈值，低于该分数的候选丢弃。
    """

    def __init__(self, models: LocalModels, vectors: VectorStore, llm: LlmClient, web: WebSearchClient,
                 rerank_min_score: float):
        self.models, self.vectors, self.llm, self.web = models, vectors, llm, web
        self.rerank_min_score = rerank_min_score

    def run(self, query: str, history: list[dict], ctx: QueryContext) -> QueryState:
        """执行一次问答，返回最终状态（含答案、来源、图片）。

        每次调用都重新构建图：节点需要绑定本次请求的 ctx（用于推送事件和计时）。
        """
        g = StateGraph(QueryState)
        nodes = {"rewrite": self._rewrite, "kb_search": self._kb_search, "hyde_search": self._hyde_search,
                 "web_search": self._web_search, "rrf_merge": self._rrf_merge, "rerank": self._rerank,
                 "generate": self._generate}
        for name, fn in nodes.items():
            g.add_node(name, self._timed(name, fn, ctx))
        g.add_edge(START, "rewrite")
        # rewrite 后分出三路检索，三路都完成后再汇合到 rrf_merge
        for b in ("kb_search", "hyde_search", "web_search"):
            g.add_edge("rewrite", b)
            g.add_edge(b, "rrf_merge")
        g.add_edge("rrf_merge", "rerank")
        g.add_edge("rerank", "generate")
        g.add_edge("generate", END)
        return g.compile().invoke({"query": query, "history": history})

    @staticmethod
    def _timed(name: str, fn, ctx: QueryContext):
        """包装节点函数：传入 ctx，并把节点耗时记录到 ctx.timings。"""
        def wrapper(state: QueryState) -> dict:
            t = time.time()
            out = fn(state, ctx)
            ctx.timings[name] = round(time.time() - t, 2)
            return out
        return wrapper

    # ---------------- 节点 ----------------

    def _rewrite(self, s: QueryState, ctx: QueryContext) -> dict:
        """结合历史改写问题，并识别内容类型和题型作为检索过滤条件。"""
        ctx.emit("progress", {"stage": "rewrite", "message": "正在理解问题…"})
        # 改写只需要知道历史在聊什么，每条截取前 300 字即可
        hist = "\n".join(f"{'用户' if m['role'] == 'user' else '助手'}：{m['content'][:300]}" for m in s["history"])
        user = f"【对话历史】\n{hist or '（无）'}\n\n【最新问题】\n{s['query']}"
        try:
            raw = self.llm.complete([{"role": "system", "content": REWRITE_SYSTEM}, {"role": "user", "content": user}],
                                    max_tokens=300, temperature=0.0, json_mode=True)
        except Exception as e:  # noqa: BLE001 —— 改写失败不影响问答
            logger.warning("问题改写失败，使用原问题：%s", e)
            raw = ""
        r = ranking.parse_rewrite(raw, s["query"])
        logger.info("改写：%s → %s | 类型=%s 题型=%s", s["query"], r["rewritten_query"], r["content_types"], r["question_types"])
        ctx.emit("progress", {"stage": "retrieve", "message": "正在检索知识库和网络…"})
        return r

    def _kb_search(self, s: QueryState, ctx: QueryContext) -> dict:
        """知识库混合检索（稠密 + 稀疏），按改写识别出的类型过滤。"""
        enc = self.models.encode([s["rewritten_query"]])
        expr = ranking.build_filter(s["content_types"], s["question_types"])
        hits = self.vectors.hybrid_search(enc.dense[0], enc.sparse[0], expr, KB_LIMIT)
        if not hits and expr:      # 过滤过严时放宽一次，避免因意图识别偏差漏掉资料
            hits = self.vectors.hybrid_search(enc.dense[0], enc.sparse[0], "", KB_LIMIT)
        return {"kb_hits": hits}

    def _hyde_search(self, s: QueryState, ctx: QueryContext) -> dict:
        """HyDE 检索：先生成一段假设答案，再用它的稠密向量检索。生成失败时跳过这一路。"""
        try:
            doc = self.llm.complete([{"role": "system", "content": HYDE_SYSTEM},
                                     {"role": "user", "content": s["rewritten_query"]}], max_tokens=250)
        except Exception as e:  # noqa: BLE001
            logger.warning("HyDE 生成失败，跳过该路：%s", e)
            return {"hyde_hits": []}
        enc = self.models.encode([doc])
        # 与知识库检索使用同一过滤条件；没识别出类型时不过滤，过严时同样放宽一次
        expr = ranking.build_filter(s["content_types"], s["question_types"])
        hits = self.vectors.dense_search(enc.dense[0], expr, HYDE_LIMIT)
        if not hits and expr:
            hits = self.vectors.dense_search(enc.dense[0], "", HYDE_LIMIT)
        return {"hyde_hits": hits}

    def _web_search(self, s: QueryState, ctx: QueryContext) -> dict:
        """联网搜索。课程、题目类问题跳过；失败时降级为只用知识库，并提示用户。"""
        if not ranking.needs_web(s["content_types"]):
            logger.info("课程/题目类问题，跳过联网搜索")
            return {"web_hits": [], "web_failed": False}
        try:
            return {"web_hits": self.web.search(s["rewritten_query"], WEB_COUNT), "web_failed": False}
        except Exception as e:  # noqa: BLE001 —— PRD F5-5：联网失败降级，不推送 error
            logger.warning("联网搜索失败，降级为仅知识库：%s", e)
            ctx.emit("progress", {"stage": "retrieve", "message": "联网搜索暂不可用，仅使用知识库作答"})
            return {"web_hits": [], "web_failed": True}

    def _rrf_merge(self, s: QueryState, ctx: QueryContext) -> dict:
        """用 RRF 融合三路检索结果并去重。"""
        return {"merged": ranking.rrf_merge([s.get("kb_hits", []), s.get("hyde_hits", []), s.get("web_hits", [])])}

    def _rerank(self, s: QueryState, ctx: QueryContext) -> dict:
        """用重排模型给候选打分，按阈值和断崖截断选出最终资料，并推送来源卡片。"""
        ctx.emit("progress", {"stage": "rerank", "message": "正在整理检索结果…"})
        items = s["merged"]
        scores = self.models.rerank(s["rewritten_query"], [ranking.rerank_text(x) for x in items])
        for x, sc in zip(items, scores):
            x["rerank"] = sc
        selected = ranking.cut_by_score(items, self.rerank_min_score)
        logger.info("重排：候选 %d → 选中 %d，分数 %s", len(items), len(selected),
                    [round(x["rerank"], 2) for x in selected])
        sources = [ranking.to_source(i, x) for i, x in enumerate(selected, 1)]
        images = ranking.collect_images(selected)
        # chunk_id 是内部字段，只存入 MongoDB，不推送给前端
        ctx.emit("sources", {"sources": [{k: v for k, v in src.items() if k != "chunk_id"} for src in sources],
                             "images": images})
        return {"selected": selected, "sources": sources, "images": images}

    def _generate(self, s: QueryState, ctx: QueryContext) -> dict:
        """流式生成答案，每段文本立即推送；收到取消信号时停止。"""
        ctx.emit("progress", {"stage": "generate", "message": "正在生成答案…"})
        context = ranking.build_context(s["selected"]) or NO_CONTEXT
        # 消息顺序：系统提示 → 历史对话 → 本轮（参考资料 + 原问题）
        messages = [{"role": "system", "content": ANSWER_SYSTEM}]
        messages += [{"role": m["role"], "content": m["content"][:HISTORY_CHARS]} for m in s["history"]]
        messages.append({"role": "user", "content": ANSWER_USER.format(context=context, query=s["query"])})
        parts: list[str] = []
        t0 = time.time()
        for delta in self.llm.stream(messages):
            if ctx.cancel.is_set():
                break
            if not parts:
                # 记录从开始生成到第一个字的时间
                ctx.timings["first_token"] = round(time.time() - t0, 2)
            parts.append(delta)
            ctx.emit("delta", {"text": delta})
        return {"answer": "".join(parts)}
