"""技术验证 S3：用 FlagEmbedding 加载本机 bge-m3 与 bge-reranker-large，各推理一次。"""
import os
import time
from pathlib import Path

import torch
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
print("torch", torch.__version__, "cuda available:", torch.cuda.is_available())
device = "cuda" if torch.cuda.is_available() else "cpu"
fp16 = device == "cuda"

from FlagEmbedding import BGEM3FlagModel, FlagReranker  # noqa: E402

t = time.time()
emb = BGEM3FlagModel(os.environ["EMBEDDING_MODEL_PATH"], use_fp16=fp16, devices=device)
print(f"bge-m3 loaded on {device} in {time.time() - t:.1f}s")
texts = ["Python 函数的默认参数怎么用？", "默认参数指在定义函数时为参数指定默认值。", "通用编程入门班适合在校生和求职者。"]
t = time.time()
out = emb.encode(texts, batch_size=16, max_length=1024, return_dense=True, return_sparse=True)
print(f"encode 3 texts: {time.time() - t:.2f}s; dense shape={out['dense_vecs'].shape}; "
      f"sparse sample={dict(list(out['lexical_weights'][0].items())[:3])}")
d = out["dense_vecs"]
print("cos(q, doc1)=%.3f  cos(q, doc2)=%.3f" % (float(d[0] @ d[1]), float(d[0] @ d[2])))

t = time.time()
rr = FlagReranker(os.environ["RERANK_MODEL_PATH"], use_fp16=fp16, devices=device)
print(f"reranker loaded in {time.time() - t:.1f}s")
t = time.time()
scores = rr.compute_score([[texts[0], texts[1]], [texts[0], texts[2]]])
print(f"rerank scores={scores} in {time.time() - t:.2f}s")
if device == "cuda":
    print("GPU mem allocated: %.2f GB" % (torch.cuda.max_memory_allocated() / 1024 ** 3))
print("S3 PASS (device=%s)" % device)
