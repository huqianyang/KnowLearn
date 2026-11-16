"""检索分阶段评测：稠密 / BM25（稀疏）/ 混合（Milvus RRF）/ 重排，以及无资料阈值扫描。

与 scripts/eval.py（端到端、走 HTTP、含改写和生成）互补：本脚本直接加载本机模型、只读查询 Milvus，
不调用大模型（不含改写与 HyDE），用于比较检索各阶段的名次，以及调整切分、重排、阈值前后逐题对比。

评测集 scripts/retrieval_eval_set.json：
- 有答案题：gold（资料名关键词，任一命中）+ facts（切片正文必须包含的文字，去空白和反斜杠后匹配，任一命中）；
  不写死切片 ID，重新切片后评测集依然有效。filter 可选，模拟改写识别出的类型过滤。
- 负例（negative: true）：知识库中没有答案，用于检查无资料阈值。

用法（需要 GPU，约 4 GB 显存；服务运行时也可以执行）：
    uv run python scripts/retrieval_eval.py [--out 结果.json]
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.infra.local_models import LocalModels  # noqa: E402
from app.infra.vector_store import OUTPUT_FIELDS, VectorStore, _hit  # noqa: E402
from app.pipelines.query import ranking  # noqa: E402

STAGES = ["dense", "bm25", "hybrid", "rerank"]
TOP_K = 10          # 每个阶段取前 10 名
CONTEXT_K = 8       # 进入上下文的条数上限（与 ranking.MAX_SELECTED 一致）
THRESHOLDS = [-3.0, -2.5, -2.0, -1.5, -1.0, -0.5]


def norm(t: str | None) -> str:
    """去掉空白和反斜杠（MinerU 输出的 Markdown 转义），用于事实匹配。"""
    return re.sub(r"[\s\\]+", "", t or "")


def is_gold(q: dict, h: dict) -> bool:
    """资料名命中 gold 任一关键词，且正文包含 facts 任一项。"""
    return any(g in h.get("file_name", "") for g in q["gold"]) and any(norm(f) in norm(h.get("content")) for f in q["facts"])


def rank_of(q: dict, hits: list[dict]) -> int | None:
    """第一个命中的名次（从 1 开始），没有命中返回 None。"""
    return next((i + 1 for i, h in enumerate(hits) if is_gold(q, h)), None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", type=Path, default=ROOT / "scripts" / "retrieval_eval_set.json")
    ap.add_argument("--out", type=Path, default=None, help="逐题结果写入 JSON，便于改动前后对比")
    a = ap.parse_args()

    s = get_settings()
    vs = VectorStore(s.milvus_uri, s.milvus_collection)
    m = LocalModels(s.embedding_model_path, s.rerank_model_path)
    m.load()

    def sparse_search(sp: dict, expr: str) -> list[dict]:
        r = vs.client.search(s.milvus_collection, [sp or {0: 1e-6}], anns_field="sparse_vector", limit=TOP_K,
                             filter=expr or "", output_fields=OUTPUT_FIELDS, search_params={"metric_type": "IP"})
        return [_hit(h) for h in r[0]]

    rows = []
    for q in json.loads(a.set.read_text(encoding="utf-8")):
        f = q.get("filter") or {}
        expr = ranking.build_filter(f.get("ct", []), f.get("qt", []))
        e = m.encode([q["q"]])
        d, sp = e.dense[0], e.sparse[0]
        res = {"dense": vs.dense_search(d, expr, TOP_K), "bm25": sparse_search(sp, expr),
               "hybrid": vs.hybrid_search(d, sp, expr, TOP_K)}
        # 与问答链路一致：题号精确命中排最前、不受阈值影响
        kb = res["hybrid"]
        codes = ranking.find_question_codes(q["q"])
        if codes:
            exact = vs.get_by_question_codes(codes)
            seen = {h["chunk_id"] for h in exact}
            kb = exact + [h for h in kb if h["chunk_id"] not in seen]
        items = ranking.rrf_merge([kb])
        for x, v in zip(items, m.rerank(q["q"], [ranking.rerank_text(x) for x in items])):
            x["rerank"] = v
        exact = [x for x in items if x.get("exact_match")]
        rest = [x for x in items if not x.get("exact_match")]
        res["rerank"] = exact + sorted(rest, key=lambda x: -x["rerank"])
        selected = (exact + ranking.cut_by_score(rest, s.rerank_min_score))[:CONTEXT_K]
        top = res["rerank"][0] if res["rerank"] else None
        row = {"id": q["id"], "kind": q["kind"], "q": q["q"], "neg": bool(q.get("negative")),
               "top_score": round(top["rerank"], 2) if top else None, "selected": len(selected),
               "top1": f"{top['file_name'][:18]} | {re.sub(chr(10), ' ', top['content'][:50])}" if top else ""}
        if not row["neg"]:
            row["ranks"] = {st: rank_of(q, res[st]) for st in STAGES}
            row["sel_rank"] = rank_of(q, selected)
            row["gold_score"] = next((round(x["rerank"], 2) for x in res["rerank"] if is_gold(q, x)), None)
            row["doc_hit"] = {st: any(any(g in h["file_name"] for g in q["gold"]) for h in res[st][:CONTEXT_K])
                              for st in STAGES}
        rows.append(row)

    pos = [r for r in rows if not r["neg"]]
    neg = [r for r in rows if r["neg"]]
    fmt = lambda v: f"{v:>6}" if v else "     -"   # noqa: E731
    print(f"{'id':4} {'类型':8}{'dense':>6}{'bm25':>6}{'hybrid':>6}{'rerank':>6}{'上下文':>6}  gold分  top分  问题")
    for r in pos:
        print(f"{r['id']:4} {r['kind']:8}" + "".join(fmt(r["ranks"][st]) for st in STAGES)
              + f"{fmt(r['sel_rank'])} {str(r['gold_score']):>6} {r['top_score']:>6}  {r['q']}")
    for r in neg:
        print(f"{r['id']:4} 负例     进上下文 {r['selected']} 条，top 分 {r['top_score']:>6}  {r['q']}  ← {r['top1']}")

    print(f"\n阶段     hit@1  hit@{CONTEXT_K}  MRR@{TOP_K}  资料级hit@{CONTEXT_K}")
    for st in STAGES:
        rk = [r["ranks"][st] for r in pos]
        print(f"{st:7} {sum(x == 1 for x in rk):3}/{len(pos)}  {sum(1 for x in rk if x and x <= CONTEXT_K):3}/{len(pos)}"
              f"  {sum(1 / x for x in rk if x) / len(pos):.3f}   {sum(r['doc_hit'][st] for r in pos)}/{len(pos)}")
    print(f"进入上下文命中 {sum(1 for r in pos if r['sel_rank'])}/{len(pos)}")
    print(f"\n无资料阈值扫描（当前 {s.rerank_min_score}）：阈值 | 有答案题被判无资料 | 无答案题被判有资料")
    for t in THRESHOLDS:
        print(f"  {t:5} | {sum(r['top_score'] < t for r in pos):2}/{len(pos)} | {sum(r['top_score'] >= t for r in neg):2}/{len(neg)}")

    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已写入 {a.out}")


if __name__ == "__main__":
    main()
