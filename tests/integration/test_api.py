"""集成测试：对运行中的服务（默认 http://127.0.0.1:8010）调用真实接口。

运行：先启动服务（uv run python -m app），再执行 uv run pytest tests/integration -m integration
服务未启动时自动跳过。测试只创建并删除自己的会话，不导入资料、不修改知识库。
"""
import json
import os

import pytest
import requests

BASE = os.environ.get("KNOWLEARN_BASE", "http://127.0.0.1:8010") + "/api/v1"
pytestmark = pytest.mark.integration   # 本文件所有测试都带 integration 标记，默认的 pytest 运行会跳过


def _up() -> bool:
    """服务是否在运行。"""
    try:
        return requests.get(f"{BASE}/health", timeout=3).json()["code"] == 0
    except Exception:  # noqa: BLE001
        return False


if not _up():
    pytest.skip("服务未启动，跳过集成测试", allow_module_level=True)


@pytest.fixture
def session():
    """为每个测试新建一个会话，测试结束后删除。"""
    sid = requests.post(f"{BASE}/sessions").json()["data"]["session_id"]
    yield sid
    requests.delete(f"{BASE}/sessions/{sid}")


def sse(sid: str, query: str) -> list[tuple[str, dict]]:
    """流式提问，返回按顺序收到的全部事件 [(事件名, 数据)]。"""
    events, ev = [], None
    with requests.post(f"{BASE}/sessions/{sid}/chat", json={"query": query, "stream": True}, stream=True, timeout=300) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines(decode_unicode=True):
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                events.append((ev, json.loads(line[6:])))
    return events


# ---------------- 健康检查 / 导入接口校验 ----------------

def test_health_all_components_ok():
    """全部组件正常。"""
    d = requests.get(f"{BASE}/health").json()["data"]
    assert d["status"] == "ok", d


def test_suggest():
    """A1：根据文件名生成默认课程名。"""
    d = requests.get(f"{BASE}/imports/suggest", params={"filename": "尚硅谷大模型技术之NLP1.0.9.docx", "doc_type": "lecture"}).json()
    assert d["data"] == {"course_name": "NLP", "project_name": None}


@pytest.mark.parametrize("fname,doc_type,data,code,status", [
    ("a.docx", "question_bank", {}, 40001, 400),          # 题库只支持 md
    ("a.xlsx", "lecture", {"course_name": "X"}, 40001, 400),   # 不支持的格式
    ("a.docx", "lecture", {}, 40002, 400),                # 讲义缺课程名
    ("a.md", "project_doc", {}, 40002, 400),              # 项目文档缺项目名
    ("a.md", "bogus", {}, 40003, 400),                    # 非法资料类型
])
def test_import_validation(fname, doc_type, data, code, status):
    """A2：参数校验失败时返回对应错误码，不创建任务。"""
    r = requests.post(f"{BASE}/imports", files={"file": (fname, b"# x")}, data={"doc_type": doc_type, **data})
    assert r.status_code == status and r.json()["code"] == code


def test_import_task_not_found_and_list_filter():
    """A3 任务不存在返回 40401；A4 按状态过滤，非法状态返回 40003。"""
    assert requests.get(f"{BASE}/imports/nope").json()["code"] == 40401
    d = requests.get(f"{BASE}/imports", params={"status": "succeeded", "page_size": 5}).json()["data"]
    assert all(x["status"] == "succeeded" for x in d["items"])
    assert requests.get(f"{BASE}/imports", params={"status": "bad"}).json()["code"] == 40003


# ---------------- 会话 ----------------

def test_session_lifecycle():
    """B1～B4：新建、出现在列表中、删除后消息接口返回 40402。"""
    s = requests.post(f"{BASE}/sessions").json()["data"]
    assert s["title"] == "新会话" and s["message_count"] == 0
    ids = [x["session_id"] for x in requests.get(f"{BASE}/sessions").json()["data"]["items"]]
    assert s["session_id"] in ids
    assert requests.delete(f"{BASE}/sessions/{s['session_id']}").json()["code"] == 0
    assert requests.get(f"{BASE}/sessions/{s['session_id']}/messages").json()["code"] == 40402


def test_chat_validation(session):
    """C1：空问题、超长问题返回 40003；会话不存在返回 40402。"""
    assert requests.post(f"{BASE}/sessions/{session}/chat", json={"query": "  "}).json()["code"] == 40003
    assert requests.post(f"{BASE}/sessions/{session}/chat", json={"query": "x" * 1001}).json()["code"] == 40003
    assert requests.post(f"{BASE}/sessions/nope/chat", json={"query": "hi"}).json()["code"] == 40402


# ---------------- 问答 ----------------

def test_stream_event_order_and_persistence(session):
    """SSE 事件顺序正确、课程类问题不联网、来源不含 chunk_id、消息落库、会话标题更新。"""
    events = sse(session, "通用编程项目班的先修要求是什么？")
    names = [e for e, _ in events]
    assert names[0] == "progress" and names[-1] == "done" and "error" not in names
    assert names.index("sources") < names.index("delta")
    stages = [d["stage"] for e, d in events if e == "progress"]
    assert stages[:2] == ["rewrite", "retrieve"] and stages[-1] == "generate"
    sources = next(d for e, d in events if e == "sources")["sources"]
    assert sources and sources[0]["content_type"] == "course_intro"
    assert not any(s["source_type"] == "web" for s in sources)          # 课程类问题不联网
    assert all("chunk_id" not in s for s in sources)
    done = next(d for e, d in events if e == "done")
    # done 中的完整答案等于全部 delta 拼接
    assert "通用编程入门班" in done["content"] and "".join(d["text"] for e, d in events if e == "delta") == done["content"]
    msgs = requests.get(f"{BASE}/sessions/{session}/messages").json()["data"]["items"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["status"] == "completed"
    title = next(x for x in requests.get(f"{BASE}/sessions").json()["data"]["items"] if x["session_id"] == session)["title"]
    assert title == "通用编程项目班的先修要求是什么？"[:20]


def test_multi_turn_resolves_reference(session):
    """多轮对话：第二轮的「它」能结合历史解析为上一轮的课程。"""
    sse(session, "通用编程项目班的详情")
    events = sse(session, "它的先修课程是什么？")
    done = next(d for e, d in events if e == "done")["content"]
    assert "通用编程入门班" in done


def test_concurrent_question_rejected_and_interrupt_saved(session):
    """回答中再次提问返回 409；断开连接后消息按 interrupted 保存，会话锁随即释放。"""
    r = requests.post(f"{BASE}/sessions/{session}/chat", json={"query": "介绍一下 FastAPI 的请求参数", "stream": True}, stream=True)
    for line in r.iter_lines(decode_unicode=True):
        # 开始输出答案时，同一会话再提问
        if line.startswith("event: delta"):
            c = requests.post(f"{BASE}/sessions/{session}/chat", json={"query": "并发", "stream": True})
            assert c.status_code == 409 and c.json()["code"] == 40901
            break
    r.close()                                           # 模拟「停止生成」
    import time
    # 服务端在后台线程中保存消息，最多等 10 秒
    for _ in range(20):
        msgs = requests.get(f"{BASE}/sessions/{session}/messages").json()["data"]["items"]
        if len(msgs) == 2:
            break
        time.sleep(0.5)
    assert msgs[1]["status"] == "interrupted"
    d = requests.post(f"{BASE}/sessions/{session}/chat", json={"query": "通用编程入门班适合什么人群？", "stream": False}).json()
    assert d["code"] == 0 and d["data"]["status"] == "completed"   # 锁已释放
