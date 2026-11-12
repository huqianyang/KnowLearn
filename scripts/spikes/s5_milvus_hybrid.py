"""技术验证 S5：Milvus 2.5.5 上按数据库设计建临时集合，验证 hybrid_search + 标量过滤，最后删除集合。

运行：uv run --no-project --with "pymilvus>=2.5,<2.6" --with python-dotenv python scripts/spikes/s5_milvus_hybrid.py
"""
import os
import random
import time
from pathlib import Path

from dotenv import load_dotenv
from pymilvus import AnnSearchRequest, DataType, MilvusClient, RRFRanker

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
COLL = f"knowlearn_spike_{int(time.time())}"
DIM = 1024

client = MilvusClient(uri=os.environ["MILVUS_URI"])
print("server version:", client.get_server_version())

schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
schema.add_field("chunk_id", DataType.VARCHAR, max_length=64, is_primary=True)
schema.add_field("content_type", DataType.VARCHAR, max_length=32)
schema.add_field("content", DataType.VARCHAR, max_length=65535)
schema.add_field("question_type", DataType.VARCHAR, max_length=32, nullable=True)
schema.add_field("file_name", DataType.VARCHAR, max_length=512)
schema.add_field("task_id", DataType.VARCHAR, max_length=64)
schema.add_field("images", DataType.JSON, nullable=True)
schema.add_field("created_at", DataType.INT64)
schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=DIM)
schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)

idx = client.prepare_index_params()
idx.add_index("dense_vector", index_type="HNSW", metric_type="COSINE", params={"M": 16, "efConstruction": 200})
idx.add_index("sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="IP")
for f in ("content_type", "question_type", "file_name", "task_id"):
    idx.add_index(f, index_type="INVERTED")

client.create_collection(COLL, schema=schema, index_params=idx, consistency_level="Strong")
print("created", COLL)
try:
    rnd = random.Random(0)
    def vec():
        v = [rnd.random() for _ in range(DIM)]
        n = sum(x * x for x in v) ** 0.5
        return [x / n for x in v]
    rows = []
    for i, (ct, qt) in enumerate([("question", "单选题"), ("question", "多选题"), ("question", "判断题"),
                                  ("course_intro", None), ("doc_chunk", None)]):
        rows.append({"chunk_id": f"c{i}", "content_type": ct, "content": f"内容{i}", "question_type": qt,
                     "file_name": "a.md", "task_id": "t1" if i < 4 else "t2",
                     "images": [{"url": "http://x/1.png", "caption": "图"}] if i == 4 else None,
                     "created_at": int(time.time() * 1000), "dense_vector": vec(),
                     "sparse_vector": {rnd.randint(0, 250000): rnd.random() for _ in range(8)}})
    print("insert:", client.insert(COLL, rows)["insert_count"])
    client.load_collection(COLL)

    q_dense, q_sparse = rows[0]["dense_vector"], rows[0]["sparse_vector"]
    flt = 'content_type == "question" and question_type in ["单选题", "多选题"]'
    reqs = [AnnSearchRequest([q_dense], "dense_vector", {"metric_type": "COSINE", "params": {"ef": 64}}, limit=10, expr=flt),
            AnnSearchRequest([q_sparse], "sparse_vector", {"metric_type": "IP"}, limit=10, expr=flt)]
    res = client.hybrid_search(COLL, reqs, ranker=RRFRanker(60), limit=10,
                               output_fields=["content_type", "question_type", "images"])
    hits = [(h["chunk_id"], h["entity"]["question_type"]) for h in res[0]]
    print("hybrid hits (filtered 单选/多选):", hits)
    assert {qt for _, qt in hits} <= {"单选题", "多选题"} and hits, "过滤失败"

    # 覆盖导入：删除 file_name 相同但 task_id 不同的旧版本
    d = client.delete(COLL, filter='file_name == "a.md" and task_id != "t2"')
    print("delete old version:", d)
    left = client.query(COLL, filter='file_name == "a.md"', output_fields=["task_id", "images"])
    print("remaining:", left)
    assert all(r["task_id"] == "t2" for r in left)
    print("S5 PASS")
finally:
    client.drop_collection(COLL)
    print("dropped", COLL)
