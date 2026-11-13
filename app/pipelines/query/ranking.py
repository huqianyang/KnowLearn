"""问答链路中的纯函数：改写结果校验、RRF 融合、重排截断、来源与上下文构建（技术方案 5.2）。

检索结果统一用 dict 表示：知识库结果带 chunk_id，网络结果带 url（没有 chunk_id）。
"""
import json
import re

CONTENT_TYPES = ("course_intro", "doc_chunk", "project_material", "question")
# 题库中实际出现的题型，改写结果中不在此列表的题型会被丢弃
QUESTION_TYPES = ("单选题", "多选题", "判断题", "简答题", "编程题", "计算题", "案例分析题", "情境分析题",
                  "证明题", "公式推导题", "完形填空题", "翻译题", "阅读理解题", "数据分析题", "材料分析题")
RRF_K = 60          # RRF 平滑常数，常用取值
SNIPPET_LEN = 200   # 来源卡片摘要的最大长度


# ---------------- 改写 ----------------

def parse_rewrite(raw: str, fallback_query: str) -> dict:
    """解析改写节点的 JSON 输出；格式不对时退回原问题、不加过滤（不能因为改写失败影响问答）。"""
    # 模型偶尔会在 JSON 前后加说明文字，只取第一个 { 到最后一个 } 之间的内容
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        data = {}
    q = str(data.get("rewritten_query") or "").strip() or fallback_query
    # 只保留合法取值，防止模型编造类型导致过滤后查不到任何结果
    cts = [c for c in data.get("content_types") or [] if c in CONTENT_TYPES]
    qts = [t for t in data.get("question_types") or [] if t in QUESTION_TYPES]
    if "选择题" in (data.get("question_types") or []):        # PRD F4-4
        qts = sorted(set(qts) | {"单选题", "多选题"}, key=QUESTION_TYPES.index)
    # 指定了题型就一定是在找题目
    if qts and "question" not in cts:
        cts = cts + ["question"]
    return {"rewritten_query": q[:500], "content_types": cts, "question_types": qts}


def build_filter(content_types: list[str], question_types: list[str]) -> str:
    """生成 Milvus 过滤表达式；不需要过滤时返回空字符串。

    例：类型 [doc_chunk, question] + 题型 [单选题] →
        content_type in ["doc_chunk", "question"] and (content_type != "question" or question_type in ["单选题"])
    """
    parts = []
    # 四种类型全选等于不过滤
    if content_types and set(content_types) != set(CONTENT_TYPES):
        parts.append("content_type in [" + ", ".join(f'"{c}"' for c in content_types) + "]")
    if question_types:
        qt = "question_type in [" + ", ".join(f'"{t}"' for t in question_types) + "]"
        # 题型只约束题目，其他内容类型不受影响
        parts.append(f'(content_type != "question" or {qt})' if content_types != ["question"] else qt)
    return " and ".join(parts)


# ---------------- 融合与重排 ----------------

def key_of(item: dict) -> str:
    """去重键：知识库结果用 chunk_id，网络结果用 url。"""
    return item.get("chunk_id") or item.get("url") or ""


def rrf_merge(result_lists: list[list[dict]], k: int = RRF_K, limit: int = 20) -> list[dict]:
    """RRF（倒数排名融合）：每条结果得分 = Σ 1 / (k + 在各路中的名次)，多路都靠前的结果得分最高。

    只看名次、不看原始分数，因此不同检索方式的分数不需要归一化。
    返回得分最高的 limit 条，每条附加 rrf 分数；同一结果保留第一次出现时的数据。
    """
    scores: dict[str, float] = {}
    first: dict[str, dict] = {}
    for results in result_lists:
        for rank, item in enumerate(results):
            key = key_of(item)
            if not key:
                continue
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            first.setdefault(key, item)
    ordered = sorted(scores, key=lambda x: scores[x], reverse=True)[:limit]
    return [{**first[x], "rrf": scores[x]} for x in ordered]


def is_web(item: dict) -> bool:
    """是否为网络搜索结果。"""
    return bool(item.get("url")) and not item.get("chunk_id")


def _gap_cut(ranked: list[dict], min_k: int, gap_ratio: float, gap_abs: float) -> list[dict]:
    """断崖截断：相邻分差大于 max(gap_abs, gap_ratio·|前一个分数|) 时截断，至少保留 min_k 个。"""
    for i in range(min_k, len(ranked)):
        prev, cur = ranked[i - 1]["rerank"], ranked[i]["rerank"]
        if prev - cur > max(gap_abs, gap_ratio * abs(prev)):
            return ranked[:i]
    return ranked


def cut_by_score(items: list[dict], min_score: float, max_k: int = 8, min_k: int = 3,
                 gap_ratio: float = 0.25, gap_abs: float = 0.5) -> list[dict]:
    """先按相关性阈值过滤；知识库结果排在网络结果之前（M4 确认的策略），两组各自做断崖截断，合计最多 max_k 个。

    阈值过滤后的数量可能少于 min_k（甚至为 0，此时回答「未找到相关资料」，PRD F5-4）。
    """
    passed = sorted((x for x in items if x["rerank"] >= min_score), key=lambda x: x["rerank"], reverse=True)
    kb = _gap_cut([x for x in passed if not is_web(x)][:max_k], min_k, gap_ratio, gap_abs)
    web = _gap_cut([x for x in passed if is_web(x)], min_k, gap_ratio, gap_abs)
    return (kb + web)[:max_k]


INTERNAL_ONLY_TYPES = {"course_intro", "question"}   # 只用本平台资料回答的内容类型


def needs_web(content_types: list[str]) -> bool:
    """课程、题目类问题只用本平台资料，不联网（M4 确认的策略）；其他问题联网作为补充。"""
    return not (content_types and set(content_types) <= INTERNAL_ONLY_TYPES)


def rerank_text(item: dict, limit: int = 1500) -> str:
    """给重排模型打分用的文本：网络结果用标题 + 摘要，知识库结果在正文前加上所属课程 / 项目 / 题库和章节。"""
    if item.get("url") and not item.get("chunk_id"):
        return f"{item.get('title', '')}\n{item.get('snippet', '')}"[:limit]
    head = " ".join(x for x in (item.get("course_name") or item.get("project_name") or item.get("bank_name"),
                                item.get("chapter")) if x)
    return f"{head}\n{item.get('content', '')}"[:limit]


# ---------------- 来源与上下文 ----------------

def to_source(index: int, item: dict) -> dict:
    """API 设计 6.1 的 Source 结构（另加 chunk_id 存入 Mongo）。"""
    score = round(float(item.get("rerank", 0.0)), 3)
    if item.get("url") and not item.get("chunk_id"):
        return {"index": index, "source_type": "web", "title": item.get("title"), "url": item["url"],
                "snippet": _snip(item.get("snippet", "")), "score": score}
    return {
        "index": index, "source_type": "kb", "chunk_id": item["chunk_id"],
        "content_type": item.get("content_type"), "course_name": item.get("course_name"),
        "project_name": item.get("project_name"), "chapter": item.get("chapter"),
        "bank_name": item.get("bank_name"), "question_code": item.get("question_code"),
        "question_type": item.get("question_type"), "file_name": item.get("file_name"),
        "file_url": item.get("file_url"), "snippet": _snip(_strip_md(item.get("content", ""))), "score": score,
    }


def collect_images(items: list[dict], limit: int = 6) -> list[dict]:
    """收集选中资料中的图片（按地址去重，最多 limit 张），记录图片来自第几条来源。"""
    out, seen = [], set()
    for i, it in enumerate(items, 1):
        for im in it.get("images") or []:
            if im.get("url") and im["url"] not in seen:
                seen.add(im["url"])
                out.append({"url": im["url"], "caption": im.get("caption") or "", "source_index": i})
    return out[:limit]


_TYPE_LABEL = {"course_intro": "课程介绍", "doc_chunk": "文档片段", "project_material": "项目资料", "question": "题目"}


def source_label(item: dict) -> str:
    """参考资料的来源说明，如「文档片段 · FastAPI · 2 路由 > 2.1 路径参数 · xxx.docx」。"""
    if item.get("url") and not item.get("chunk_id"):
        return f"网络 · {item.get('title', '')} · {item['url']}"
    parts = [_TYPE_LABEL.get(item.get("content_type"), "资料")]
    parts += [x for x in (item.get("course_name") or item.get("project_name"), item.get("bank_name"),
                          item.get("question_code"), item.get("chapter"), item.get("file_name")) if x]
    return " · ".join(parts)


def build_context(items: list[dict], max_chars: int = 12000) -> str:
    """把选中的资料拼成带编号的参考资料文本，总长度不超过 max_chars（超出部分截断）。

    编号 [n] 与来源卡片的编号一致，模型在答案中用 [n] 引用。
    """
    sep = "\n\n"
    blocks, used = [], 0
    for i, it in enumerate(items, 1):
        body = it.get("snippet", "") if (it.get("url") and not it.get("chunk_id")) else it.get("content", "")
        block = f"[{i}] 来源：{source_label(it)}\n{body}"
        room = max_chars - used - (len(sep) if blocks else 0)   # 分隔符也计入长度上限
        if room <= 0:
            break
        blocks.append(block[:room])
        used += len(blocks[-1]) + (len(sep) if len(blocks) > 1 else 0)
    return sep.join(blocks)


def _strip_md(s: str) -> str:
    """去掉常见 Markdown 标记，让来源摘要更易读。"""
    return re.sub(r"[#*`>|]+", " ", s)


def _snip(s: str) -> str:
    """合并空白，超过 SNIPPET_LEN 时截断并加省略号。"""
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= SNIPPET_LEN else s[:SNIPPET_LEN - 1] + "…"
