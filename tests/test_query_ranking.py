"""M4：问答链路纯函数——改写解析、过滤表达式、RRF、知识库优先截断、联网策略、来源与上下文。"""
from app.pipelines.query import ranking as r


def kb(cid, score=0.0, **kw):
    """构造一条知识库检索结果（带 chunk_id）。"""
    return {"chunk_id": cid, "rerank": score, "content_type": "doc_chunk", "content": f"内容{cid}", **kw}


def web(url, score=0.0):
    """构造一条网络搜索结果（只有 url，没有 chunk_id）。"""
    return {"url": url, "title": f"网页{url}", "snippet": "摘要", "rerank": score}


# ---------- 改写 ----------

def test_parse_rewrite_normal_and_choice_mapping():
    """「选择题」展开为单选题 + 多选题。"""
    out = r.parse_rewrite('{"rewritten_query":"函数相关的题目","content_types":["question"],"question_types":["选择题"]}', "原问题")
    assert out == {"rewritten_query": "函数相关的题目", "content_types": ["question"], "question_types": ["单选题", "多选题"]}


def test_parse_rewrite_drops_unknown_values_and_adds_question_type():
    """丢弃非法取值；指定了题型时自动加上 question 类型；能处理 ```json 包裹。"""
    out = r.parse_rewrite('```json\n{"rewritten_query":"x","content_types":["bogus"],"question_types":["判断题","作文题"]}\n```', "q")
    assert out["content_types"] == ["question"] and out["question_types"] == ["判断题"]


def test_parse_rewrite_falls_back_on_garbage():
    """输出不是 JSON 时退回原问题、不加过滤。"""
    assert r.parse_rewrite("不是 JSON", "原问题") == {"rewritten_query": "原问题", "content_types": [], "question_types": []}


# ---------- 过滤表达式 ----------

def test_build_filter():
    """各种组合下生成的 Milvus 过滤表达式。"""
    assert r.build_filter([], []) == ""
    assert r.build_filter(list(r.CONTENT_TYPES), []) == ""           # 全部类型等于不过滤
    assert r.build_filter(["question"], ["单选题", "多选题"]) == \
        'content_type in ["question"] and question_type in ["单选题", "多选题"]'
    # 题型只约束题目，课程介绍不受影响
    assert r.build_filter(["course_intro", "question"], ["判断题"]) == \
        'content_type in ["course_intro", "question"] and (content_type != "question" or question_type in ["判断题"])'


# ---------- RRF ----------

def test_rrf_merge_rewards_items_found_by_multiple_routes():
    """被多路同时召回的结果排名最高。"""
    a, b, c = kb("a"), kb("b"), kb("c")
    merged = r.rrf_merge([[a, b], [b, c], [web("u1")]])
    assert merged[0]["chunk_id"] == "b"                 # 两路都召回
    assert {r.key_of(x) for x in merged} == {"a", "b", "c", "u1"}


def test_rrf_merge_dedupes_and_limits():
    """融合结果去重，并限制条数。"""
    items = [kb(str(i)) for i in range(30)]
    merged = r.rrf_merge([items, items], limit=20)
    assert len(merged) == 20 and len({x["chunk_id"] for x in merged}) == 20


# ---------- 截断（知识库优先） ----------

def test_cut_threshold_filters_everything_when_irrelevant():
    """全部低于阈值时返回空列表（回答「没有找到相关资料」）。"""
    assert r.cut_by_score([kb("a", -5), web("u", -6)], min_score=-3) == []


def test_cut_kb_before_web_even_if_web_scores_higher():
    """即使网络结果分数更高，知识库结果也排在前面。"""
    out = r.cut_by_score([web("u1", 1.08), kb("q004", 0.08), kb("m1", -0.33), web("u2", 0.3)], min_score=-3)
    assert [r.key_of(x) for x in out] == ["q004", "m1", "u1", "u2"]


def test_cut_gap_detection_after_min_k():
    """保留至少 min_k 条之后，遇到分数断崖就截断。"""
    items = [kb("a", 3.0), kb("b", 2.8), kb("c", 2.6), kb("d", 2.5), kb("e", -1.0)]
    out = r.cut_by_score(items, min_score=-3)
    assert [x["chunk_id"] for x in out] == ["a", "b", "c", "d"]      # d→e 落差 3.5 被截断


def test_cut_respects_max_k():
    """合计不超过 max_k 条，知识库结果占满时网络结果被挤掉。"""
    out = r.cut_by_score([kb(str(i), 1 - i * 0.01) for i in range(12)] + [web("u", 0.5)], min_score=-3, max_k=8)
    assert len(out) == 8 and all("chunk_id" in x for x in out)


def test_needs_web():
    """只有课程 / 题目类问题不联网；不限类型或含其他类型时联网。"""
    assert not r.needs_web(["question"])
    assert not r.needs_web(["course_intro"])
    assert not r.needs_web(["course_intro", "question"])
    assert r.needs_web([])
    assert r.needs_web(["doc_chunk"])
    assert r.needs_web(["question", "doc_chunk"])


# ---------- 来源与上下文 ----------

def test_to_source_kb_and_web():
    """知识库来源和网络来源的字段结构。"""
    s = r.to_source(1, kb("c1", 0.1234, course_name="Python", chapter="3 函数", file_name="a.docx", file_url="http://m/a"))
    assert s["source_type"] == "kb" and s["chunk_id"] == "c1" and s["score"] == 0.123
    assert s["course_name"] == "Python" and s["bank_name"] is None        # 不适用的字段返回 None，不省略
    w = r.to_source(2, web("http://x"))
    assert w["source_type"] == "web" and w["url"] == "http://x" and "chunk_id" not in w


def test_snippet_truncated_to_200():
    """来源摘要截断为 200 字并以省略号结尾。"""
    s = r.to_source(1, kb("c", content="字" * 500))
    assert len(s["snippet"]) == 200 and s["snippet"].endswith("…")


def test_collect_images_dedup_and_source_index():
    """图片按地址去重，并记录首次出现的来源编号。"""
    items = [kb("a", images=[{"url": "u1", "caption": "c1"}]), kb("b", images=[{"url": "u1", "caption": "c1"}, {"url": "u2", "caption": "c2"}])]
    assert r.collect_images(items) == [{"url": "u1", "caption": "c1", "source_index": 1},
                                       {"url": "u2", "caption": "c2", "source_index": 2}]


def test_build_context_numbering_and_limit():
    """参考资料按 [n] 编号并带来源说明，总长度不超过上限。"""
    ctx = r.build_context([kb("a", course_name="Python", chapter="1 概述", file_name="p.docx"), web("http://w")])
    assert ctx.startswith("[1] 来源：文档片段 · Python · 1 概述 · p.docx\n内容a")
    assert "[2] 来源：网络 · 网页http://w · http://w\n摘要" in ctx
    assert len(r.build_context([kb(str(i), content="x" * 5000) for i in range(5)], max_chars=12000)) <= 12000
