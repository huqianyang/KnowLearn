"""健壮性审计修复的回归测试：改写结果校验、降级链、上下文分配、字节长度、覆盖导入顺序、临时目录清理等。

全部使用内存中的假依赖，不连接真实的 Milvus / MongoDB / MinIO / 大模型。
"""
import logging
import os
import types
from pathlib import Path

import pytest

from app.core.errors import BizError
from app.core.files import remove_tree, sweep_temp_dir
from app.infra.local_models import EncodeResult, LocalModels
from app.infra.mongo import IMPORT_TASKS, KB_FILES
from app.infra.vector_store import VARCHAR_LIMITS, fit_row, truncate_utf8, utf8_len
from app.infra.websearch import normalize_pages
from app.parsers.models import Chunk
from app.pipelines.importing.graph import Deps, ImportPipeline
from app.pipelines.query import ranking
from app.pipelines.query.graph import QueryContext, QueryPipeline
from app.services.import_service import ImportService

RERANK_PATH = Path(r"D:\ai_models\rerank\rerank\BAAI\bge-reranker-large")


# ---------------- A3：改写结果不可信 ----------------

@pytest.mark.parametrize("raw,expect", [
    ('{"rewritten_query":"q","content_types":5,"question_types":[]}', ("q", [], [])),
    ('{"rewritten_query":"q","content_types":[],"question_types":true}', ("q", [], [])),
    ('{"rewritten_query":"null","content_types":null,"question_types":null}', ("原问题", [], [])),
    ('{"rewritten_query":"None","content_types":["question"],"question_types":["None"]}', ("原问题", ["question"], [])),
    ('{"rewritten_query":{"x":1},"content_types":[],"question_types":[]}', ("原问题", [], [])),
    ('{"rewritten_query":"q","content_types":"doc_chunk","question_types":"单选题"}', ("q", ["doc_chunk", "question"], ["单选题"])),
    ('{"rewritten_query":"q","content_types":["doc_chunk","doc_chunk"],"question_types":[]}', ("q", ["doc_chunk"], [])),
    ('[1, 2]', ("原问题", [], [])),
])
def test_parse_rewrite_rejects_bad_types(raw, expect):
    """模型返回错误类型、字符串 null、重复值时不抛异常，按空值或原问题处理。"""
    r = ranking.parse_rewrite(raw, "原问题")
    assert (r["rewritten_query"], r["content_types"], r["question_types"]) == expect


def test_find_question_codes():
    """题号可以紧挨中文，去重且保持顺序；普通单词不会被误识别。"""
    assert ranking.find_question_codes("看一下general_purpose_programming_bank_q021这题和 a_bank_q1，"
                                       "还有 general_purpose_programming_bank_q021") == \
        ["general_purpose_programming_bank_q021", "a_bank_q1"]
    assert ranking.find_question_codes("bank 里的 q1 题") == []


# ---------------- B5：上下文公平分配 ----------------

def test_build_context_long_item_does_not_crowd_out_others():
    """第 1 条是上万字的代码块时，其余 7 条仍然出现在上下文中，总长度不超过上限。"""
    items = [{"chunk_id": "big", "content_type": "project_material", "content": "x" * 15000}]
    items += [{"chunk_id": str(i), "content_type": "doc_chunk", "content": f"内容{i}" * 50} for i in range(7)]
    ctx = ranking.build_context(items, max_chars=12000)
    assert len(ctx) <= 12000
    assert all(f"[{i}] 来源" in ctx for i in range(1, 9))
    assert ranking.TRUNCATED_MARK in ctx
    # 短资料不会被截断
    assert "内容6" * 50 in ctx


# ---------------- A5：联网结果只保留 http(s) ----------------

def test_normalize_pages_drops_non_http_urls():
    """javascript: 等地址会进入来源卡片的链接，必须丢弃。"""
    pages = [{"title": "坏", "url": "javascript:alert(1)"}, {"url": " https://a.com/x ", "snippet": " s "},
             {"title": "无地址"}, {"title": "f", "url": "ftp://x"}]
    assert normalize_pages(pages, 5) == [{"title": "https://a.com/x", "url": "https://a.com/x", "snippet": "s"}]


# ---------------- B1：降级链 ----------------

HIT = {"chunk_id": "c1", "content_type": "doc_chunk", "content": "路径参数用类型注解校验", "course_name": "FastAPI",
       "file_name": "f.docx", "file_url": "http://u", "images": None, "score": 0.9}


class _Boom(RuntimeError):
    pass


def _pipeline(fail: str = "", rewrite_raw: str | None = None, codes_rows: list | None = None):
    """构造全部依赖都是假的问答图；fail 指定哪个依赖抛异常。"""
    models = types.SimpleNamespace(
        encode=lambda t: EncodeResult([[0.0]], [{1: 1.0}]),
        rerank=lambda q, ps: (_ for _ in ()).throw(_Boom("rerank")) if fail == "rerank" else [1.0] * len(ps))
    calls = {"codes": None}

    def dense_search(*a):
        if fail == "hyde_milvus":
            raise _Boom("milvus timeout")
        return [dict(HIT)]

    def get_by_codes(codes):
        calls["codes"] = codes
        return codes_rows or []

    vectors = types.SimpleNamespace(hybrid_search=lambda *a: [dict(HIT)], dense_search=dense_search,
                                    get_by_question_codes=get_by_codes)

    def complete(msgs, json_mode=False, **kw):
        assert kw.get("timeout"), "改写和 HyDE 必须设置单次超时"
        if json_mode:
            return rewrite_raw if rewrite_raw is not None else \
                '{"rewritten_query":"q","content_types":["doc_chunk"],"question_types":[]}'
        return "假设答案"

    llm = types.SimpleNamespace(complete=complete, stream=lambda msgs, **kw: iter(["答案[1]"]))
    web = types.SimpleNamespace(search=lambda q, n: [])
    return QueryPipeline(models, vectors, llm, web, -3.0), calls


@pytest.mark.parametrize("fail", ["", "rerank", "hyde_milvus"])
def test_query_degrades_instead_of_failing(fail):
    """重排失败按 RRF 顺序作答；HyDE 路径的 Milvus 出错只跳过这一路。"""
    p, _ = _pipeline(fail)
    st = p.run("问题", [], QueryContext(emit=lambda e, d: None))
    assert st["answer"] == "答案[1]"
    assert [s["chunk_id"] for s in st["sources"]] == ["c1"]


def test_query_survives_malformed_rewrite():
    """改写返回错误类型时整条问答不失败（审计实测：原先 content_types 为数字会让问答报错）。"""
    p, _ = _pipeline(rewrite_raw='{"rewritten_query":"q","content_types":5,"question_types":true}')
    assert p.run("问题", [], QueryContext(emit=lambda e, d: None))["answer"] == "答案[1]"


@pytest.mark.parametrize("q,expect", [
    ("通用编程项目班的详情", ["course_intro"]), ("班组长带人训练营适合什么年级", ["course_intro"]),
    ("查询函数相关的选择题", ["question"]), ("x_bank_q004 的答案", ["question"]),
    ("Git 怎么撤销上一次提交", []), ("最新版本的 Python 有什么新特性", []),
])
def test_guess_content_types(q, expect):
    """改写失败时的关键词兜底：只认明确的课程 / 题目说法，其他问题不限类型。"""
    assert ranking.guess_content_types(q) == expect


def test_rewrite_failure_routes_course_question_away_from_web():
    """改写超时时，课程类问题按关键词判为课程介绍，不去联网（审计 B3 在真实服务上复现过）。"""
    p, _ = _pipeline()
    web_calls = []
    p.web = types.SimpleNamespace(search=lambda q, n: web_calls.append(q) or [])

    def timeout(msgs, json_mode=False, **kw):
        if json_mode:
            raise TimeoutError("Request timed out.")
        return "假设答案"

    p.llm = types.SimpleNamespace(complete=timeout, stream=lambda msgs, **kw: iter(["答案"]))
    st = p.run("通用编程项目班的详情", [], QueryContext(emit=lambda e, d: None))
    assert st["content_types"] == ["course_intro"] and web_calls == []


def test_question_code_exact_lookup_ranks_first():
    """问题中带题号时，按题号精确查到的题目排在知识库结果最前面。"""
    q = {"chunk_id": "q21", "content_type": "question", "content": "count_vowels", "question_code": "x_bank_q021",
         "file_name": "题目资料.md", "file_url": "http://u", "score": 1.0, "exact_match": True}
    p, calls = _pipeline(codes_rows=[q])
    st = p.run("x_bank_q021 这道题怎么做", [], QueryContext(emit=lambda e, d: None))
    assert calls["codes"] == ["x_bank_q021"]
    assert st["sources"][0]["chunk_id"] == "q21"


# ---------------- B6：重排尾部窗口 ----------------

@pytest.mark.skipif(not RERANK_PATH.exists(), reason="本机没有 bge-reranker-large 分词器")
def test_rerank_tail_window_keeps_header_and_tail():
    """超过 512 token 的候选生成「第一行 + 尾部」窗口，窗口本身不超过上限；短候选不生成窗口。"""
    from transformers import AutoTokenizer

    m = LocalModels(Path("."), RERANK_PATH)
    m._rr_tokenizer = AutoTokenizer.from_pretrained(str(RERANK_PATH))
    long = "Pandas > 3.2.4 Series的常用方法\n" + "前文内容。" * 300 + "最后一句：arr7.hist(bins=3) 绘制直方图"
    wins = m._tail_windows("pandas 怎么画直方图", [long, "短\n内容"])
    assert [i for i, _ in wins] == [0]
    text = wins[0][1]
    assert text.startswith("Pandas > 3.2.4 Series的常用方法\n") and text.endswith("绘制直方图")
    n = len(m._rr_tokenizer("pandas 怎么画直方图", text)["input_ids"])
    assert n <= LocalModels.RERANK_MAX_TOKENS


def test_rerank_takes_max_of_head_and_tail(monkeypatch):
    """最终分数取头部和尾部窗口的较高分。"""
    m = LocalModels(Path("."), Path("."))
    monkeypatch.setattr(m, "_tail_windows", lambda q, ps: [(0, "尾部")])
    monkeypatch.setattr(m, "_score", lambda q, ps, bs: [-1.0, 0.5, 3.0])   # 两个原文 + 一个尾部窗口
    assert m.rerank("q", ["长文本", "短文本"]) == [3.0, 0.5]


# ---------------- A1：按 UTF-8 字节限制 ----------------

def test_truncate_utf8_never_splits_a_character():
    """按字节截断不会切出半个汉字。"""
    s = "汉" * 100
    t = truncate_utf8(s, 256)
    assert utf8_len(t) <= 256 and t == "汉" * 85


def test_fit_row_truncates_safe_fields_and_reports_others():
    """章节等可截断字段被截到上限内；文件名超限无法截断，报告出来。"""
    row = {"chapter": "章" * 400, "project_name": "项" * 90, "file_name": "文" * 200, "content": "x"}
    over = fit_row(row)
    assert over == ["file_name"]
    assert utf8_len(row["chapter"]) <= VARCHAR_LIMITS["chapter"]
    assert utf8_len(row["project_name"]) <= VARCHAR_LIMITS["project_name"]


def _service(tmp_path) -> ImportService:
    s = types.SimpleNamespace(minio_public_base="http://192.168.10.100:9000", minio_bucket="knowlearn",
                              temp_dir=tmp_path, max_upload_bytes=1 << 20, max_upload_mb=1)
    return ImportService(s, types.SimpleNamespace(db={}))


@pytest.mark.parametrize("name,ok", [("文" * 100 + ".md", True), ("文" * 101 + ".md", False),
                                     ("a" * 300 + ".md", True), ("文" * 171 + ".md", False)])
def test_filename_length_checked_in_bytes(tmp_path, name, ok):
    """文件名按「编码后的 file_url ≤ 1024 字节、file_name ≤ 512 字节」校验。

    以生产配置（桶 knowlearn）计算：汉字 URL 编码后占 9 字节，.md 文件名最多 100 个汉字。
    """
    svc = _service(tmp_path)
    if ok:
        svc._check_filename_length(name)
    else:
        with pytest.raises(BizError):
            svc._check_filename_length(name)


# ---------------- C2：覆盖导入的提交点 ----------------

class _Col:
    """极简的内存集合，只支持本测试用到的查询。"""

    def __init__(self):
        self.docs: list[dict] = []

    def find_one(self, q, proj=None):
        return next((d for d in self.docs if all(d.get(k) == v for k, v in q.items())), None)

    def update_one(self, q, upd, upsert=False):
        d = self.find_one(q)
        if d is None:
            d = dict(q, **upd.get("$setOnInsert", {}))
            self.docs.append(d)
        d.update(upd.get("$set", {}))


class _Vectors:
    def __init__(self):
        self.rows: list[dict] = []
        self.fail_delete_old = False

    def insert(self, rows):
        self.rows += rows
        return len(rows)

    def delete(self, expr):
        # 只支持两种表达式：task_id == "x"、file_name == "f" and task_id != "x"
        if " and " in expr:
            if self.fail_delete_old:
                raise _Boom("milvus delete")
            keep = expr.split("task_id != ")[1].strip('"')
            before = len(self.rows)
            self.rows = [r for r in self.rows if r["task_id"] == keep]
            return before - len(self.rows)
        tid = expr.split("== ")[1].strip('"')
        before = len(self.rows)
        self.rows = [r for r in self.rows if r["task_id"] != tid]
        return before - len(self.rows)


def _import_pipeline(storage_fails: bool):
    db = {KB_FILES: _Col(), IMPORT_TASKS: _Col()}

    def delete_prefix(p):
        if storage_fails:
            raise _Boom("MinIO 暂时不可用")
        return 0

    deps = Deps(None, types.SimpleNamespace(db=db), types.SimpleNamespace(delete_prefix=delete_prefix),
                _Vectors(), None, None, None)
    p = ImportPipeline(deps)
    p.tracker = types.SimpleNamespace(start=lambda *a: None, done=lambda *a: None)
    return p, db


def _version(tid: str) -> dict:
    chunks = [Chunk("doc_chunk", f"{tid}-{i}", "e", course_name="C") for i in range(3)]
    return {"task_id": tid, "file_id": "F", "file_name": "同名.md", "file_url": "http://u", "doc_type": "lecture",
            "course_name": "C", "chunks": chunks, "dense": [[0.1]] * 3, "sparse": [{1: .5}] * 3}


@pytest.mark.parametrize("fail", ["minio", "milvus"])
def test_overwrite_keeps_new_version_when_old_cleanup_fails(fail):
    """新版本生效后，删除旧版本失败不能让任务失败，更不能让 run() 把新版本也删掉（审计 C2 实测原先会丢光）。"""
    p, db = _import_pipeline(storage_fails=(fail == "minio"))
    p._node_store(_version("v1"))
    db[IMPORT_TASKS].docs.append({"_id": "v1", "file_id": "F"})
    p.d.vectors.fail_delete_old = fail == "milvus"
    p._node_store(_version("v2"))                      # 不抛异常
    p._cleanup_failed("v2", "F")                       # 即使之后某步出错走到失败清理，也不能删生效版本
    assert db[KB_FILES].find_one({"file_name": "同名.md"})["active_task_id"] == "v2"
    assert sum(1 for r in p.d.vectors.rows if r["task_id"] == "v2") == 3


def test_cleanup_failed_removes_rows_of_inactive_task():
    """未生效的失败任务，其写入的切片会被清理。"""
    p, db = _import_pipeline(storage_fails=False)
    p.d.vectors.rows = [{"task_id": "t9", "file_name": "x"}]
    p._cleanup_failed("t9", "F")
    assert p.d.vectors.rows == []


def test_store_rejects_overlong_file_name_with_readable_error():
    """文件名超限时给出中文错误，不把英文的 Milvus 报错展示给运营。"""
    from app.pipelines.importing.graph import ImportFailed

    p, _ = _import_pipeline(storage_fails=False)
    s = _version("v1")
    s["file_name"] = "文" * 200 + ".md"
    with pytest.raises(ImportFailed, match="file_name"):
        p._node_store(s)
    assert p.d.vectors.rows == []


# ---------------- 健康检查依赖的方法 ----------------

def test_infra_clients_expose_health_methods():
    """健康检查和关闭流程调用的方法必须存在（曾因编辑把 Storage.ping 挤出类，导致健康检查 500）。"""
    from app.infra.mongo import Mongo
    from app.infra.storage import Storage
    from app.infra.vector_store import VectorStore

    for cls, names in [(Storage, ["ping", "public_url"]), (VectorStore, ["ping", "close"]), (Mongo, ["ping", "close"])]:
        for n in names:
            assert callable(getattr(cls, n, None)), f"{cls.__name__}.{n} 不存在"


# ---------------- A2：临时目录清理 ----------------

def test_remove_tree_reports_locked_file(tmp_path, caplog):
    """文件被占用时删不干净要记录日志并返回 False（原先 ignore_errors=True 静默残留）。"""
    d = tmp_path / "task"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "b.txt").write_text("x")
    if os.name != "nt":
        pytest.skip("文件占用导致无法删除只在 Windows 上出现")
    with open(d / "sub" / "b.txt"), caplog.at_level(logging.WARNING):
        assert remove_tree(d, retries=2, delay=0.01) is False
    assert "未能完全删除" in caplog.text
    assert remove_tree(d) is True and not d.exists()


def test_sweep_temp_dir_keeps_listed_tasks(tmp_path):
    """启动清扫删除残留目录和文件，保留指定任务的目录。"""
    for n in ("old1", "old2", "keep"):
        (tmp_path / n).mkdir()
    (tmp_path / "stray.txt").write_text("x")
    assert sweep_temp_dir(tmp_path, keep={"keep"}) == 3
    assert [p.name for p in tmp_path.iterdir()] == ["keep"]
