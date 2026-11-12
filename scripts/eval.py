"""评测与性能基线（开发计划 M5，PRD 非功能需求「实时性」）。

对 eval_questions.json 中每个问题新建会话、以流式方式提问，记录：
- 首个进度事件时延（PRD：1 秒内显示进度）
- 首字时延（提交到第一个 delta）、总耗时
- 服务端各节点耗时（从助手消息的 timings 读取）
- 来源是否命中预期内容类型、答案或来源中是否包含预期文字、课程/题目类问题是否没有网络来源

用法：uv run python scripts/eval.py [--base http://127.0.0.1:8010] [--out docs/05_测试/评测结果.md]
"""
import argparse
import json
import statistics
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent


def ask(base: str, query: str) -> dict:
    sid = requests.post(f"{base}/api/v1/sessions", timeout=10).json()["data"]["session_id"]
    t0 = time.time()
    first_progress = first_delta = None
    sources, content, error = [], "", None
    with requests.post(f"{base}/api/v1/sessions/{sid}/chat", json={"query": query, "stream": True},
                       stream=True, timeout=300) as r:
        ev = None
        for line in r.iter_lines(decode_unicode=True):
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                d = json.loads(line[6:])
                now = time.time() - t0
                if ev == "progress" and first_progress is None:
                    first_progress = now
                elif ev == "delta" and first_delta is None:
                    first_delta = now
                elif ev == "sources":
                    sources = d["sources"]
                elif ev == "done":
                    content = d["content"]
                elif ev == "error":
                    error = d["message"]
    total = time.time() - t0
    msgs = requests.get(f"{base}/api/v1/sessions/{sid}/messages", timeout=10).json()["data"]["items"]
    return {"session_id": sid, "first_progress": first_progress, "first_delta": first_delta, "total": total,
            "sources": sources, "content": content, "error": error, "status": msgs[-1].get("status")}


def check(q: dict, r: dict, timings: dict) -> dict:
    types = [s.get("content_type") for s in r["sources"] if s["source_type"] == "kb"]
    hay = r["content"] + json.dumps(r["sources"], ensure_ascii=False)
    return {
        "type_ok": bool(types) and types[0] in q["expect_types"],
        "text_ok": all(t.lower() in hay.lower() for t in q.get("expect_text", [])),
        "web_ok": not (q.get("no_web") and any(s["source_type"] == "web" for s in r["sources"])),
        "cited": "[1]" in r["content"],
        "timings": timings,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8010")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--keep-sessions", action="store_true", help="保留评测产生的会话（默认评测后删除）")
    a = ap.parse_args()
    questions = json.loads((ROOT / "eval_questions.json").read_text(encoding="utf-8"))
    rows = []
    for q in questions:
        r = ask(a.base, q["query"])
        c = check(q, r, {})   # 服务端各节点耗时见日志「问答结束 timings=…」与 messages.timings
        rows.append((q, r, c))
        flag = "✅" if (c["type_ok"] and c["text_ok"] and c["web_ok"] and not r["error"]) else "❌"
        print(f"{flag} {q['id']:<3} 进度 {r['first_progress'] or 0:4.2f}s  首字 {r['first_delta'] or 0:5.1f}s  总 {r['total']:5.1f}s  "
              f"来源 {len(r['sources'])}（首条 {r['sources'][0].get('content_type') or 'web' if r['sources'] else '无'}）"
              f"  类型{'✓' if c['type_ok'] else '✗'} 内容{'✓' if c['text_ok'] else '✗'} 联网{'✓' if c['web_ok'] else '✗'}  {q['query']}")
        if not a.keep_sessions:
            requests.delete(f"{a.base}/api/v1/sessions/{r['session_id']}", timeout=10)

    fp = [r["first_progress"] for _, r, _ in rows if r["first_progress"] is not None]
    fd = [r["first_delta"] for _, r, _ in rows if r["first_delta"] is not None]
    tt = [r["total"] for _, r, _ in rows]
    passed = sum(1 for q, r, c in rows if c["type_ok"] and c["text_ok"] and c["web_ok"] and not r["error"])

    def pct(xs, p):
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]

    summary = (f"通过 {passed}/{len(rows)}；首个进度事件 最大 {max(fp):.2f}s；"
               f"首字时延 中位数 {statistics.median(fd):.1f}s / P90 {pct(fd, 90):.1f}s / 最大 {max(fd):.1f}s；"
               f"总耗时 中位数 {statistics.median(tt):.1f}s / P90 {pct(tt, 90):.1f}s / 最大 {max(tt):.1f}s")
    print("\n" + summary)

    if a.out:
        lines = ["# KnowLearn 评测结果", "", f"- 时间：{time.strftime('%Y-%m-%d %H:%M')}", f"- 汇总：{summary}", "",
                 "| # | 场景 | 问题 | 首个进度 | 首字 | 总耗时 | 首条来源 | 类型 | 内容 | 不联网 | 结果 |",
                 "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for q, r, c in rows:
            ok = c["type_ok"] and c["text_ok"] and c["web_ok"] and not r["error"]
            first = (r["sources"][0].get("content_type") or "web") if r["sources"] else "无"
            lines.append(f"| {q['id']} | {q['scenario']} | {q['query']} | {r['first_progress'] or 0:.2f}s | {r['first_delta'] or 0:.1f}s "
                         f"| {r['total']:.1f}s | {first} | {'✓' if c['type_ok'] else '✗'} | {'✓' if c['text_ok'] else '✗'} "
                         f"| {'✓' if c['web_ok'] else '✗'} | {'✅' if ok else '❌'} |")
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"已写入 {a.out}")


if __name__ == "__main__":
    main()
