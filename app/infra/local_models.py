"""本机模型：bge-m3（稠密 + 稀疏向量）与 bge-reranker-large。只用 GPU（开发计划 P2）。

导入和问答共用一把 GPU 锁，避免并发推理导致显存溢出（技术方案第 3 章）。
"""
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class GpuUnavailableError(RuntimeError):
    pass


@dataclass
class EncodeResult:
    dense: list[list[float]]
    sparse: list[dict[int, float]]


class LocalModels:
    def __init__(self, embedding_path: Path, rerank_path: Path):
        self.embedding_path = embedding_path
        self.rerank_path = rerank_path
        self.lock = threading.Lock()
        self._embedder = None
        self._reranker = None

    def load(self) -> None:
        import torch  # 延迟导入，加快无关模块的测试速度

        if not torch.cuda.is_available():
            raise GpuUnavailableError("CUDA 不可用：本项目的本机模型只在 GPU 上运行，请检查显卡驱动和 torch 版本")
        for p in (self.embedding_path, self.rerank_path):
            if not Path(p).exists():
                raise FileNotFoundError(f"模型目录不存在：{p}")

        from FlagEmbedding import BGEM3FlagModel
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        try:
            t = time.time()
            self._embedder = BGEM3FlagModel(str(self.embedding_path), use_fp16=True, devices="cuda")
            logger.info("bge-m3 已加载到 GPU（%.1fs）", time.time() - t)
            t = time.time()
            # reranker 直接用 transformers 加载：FlagReranker 在当前 transformers 版本下对超长输入的截断有 bug，
            # 且 bge-reranker-large（XLM-R large）最多只支持 512 token，超出会触发 CUDA 越界断言（M4 实测）
            self._rr_tokenizer = AutoTokenizer.from_pretrained(str(self.rerank_path))
            self._reranker = AutoModelForSequenceClassification.from_pretrained(str(self.rerank_path)).half().cuda().eval()
            logger.info("reranker 已加载到 GPU（%.1fs）", time.time() - t)
            # 预热，避免第一次请求变慢；用超长文本确认截断生效
            self.encode(["预热"])
            self.rerank("预热", ["预热" * 2000])
        except torch.cuda.OutOfMemoryError as e:
            free, total = torch.cuda.mem_get_info()
            raise GpuUnavailableError(
                f"显存不足，无法加载本机模型（当前空闲 {free / 1024**3:.2f} GB / 共 {total / 1024**3:.2f} GB，"
                f"两个模型约需 2.1 GB）。请先关闭占用显存的程序") from e
        logger.info("GPU 显存占用峰值 %.2f GB", torch.cuda.max_memory_allocated() / 1024 ** 3)

    @property
    def embedder_ready(self) -> bool:
        return self._embedder is not None

    @property
    def reranker_ready(self) -> bool:
        return self._reranker is not None

    def encode(self, texts: list[str], batch_size: int = 16, max_length: int = 2048) -> EncodeResult:
        with self.lock:
            out = self._embedder.encode(texts, batch_size=batch_size, max_length=max_length,
                                        return_dense=True, return_sparse=True)
        dense = [v.astype("float32").tolist() for v in out["dense_vecs"]]
        sparse = [{int(k): float(v) for k, v in w.items()} for w in out["lexical_weights"]]
        return EncodeResult(dense=dense, sparse=sparse)

    RERANK_MAX_TOKENS = 512     # bge-reranker-large 的位置编码上限

    def rerank(self, query: str, passages: list[str], batch_size: int = 16) -> list[float]:
        """返回 bge-reranker 原始 logit 分数（越大越相关）。"""
        import torch

        if not passages:
            return []
        scores: list[float] = []
        with self.lock, torch.inference_mode():
            for i in range(0, len(passages), batch_size):
                batch = passages[i:i + batch_size]
                enc = self._rr_tokenizer([query] * len(batch), batch, padding=True, truncation="only_second",
                                         max_length=self.RERANK_MAX_TOKENS, return_tensors="pt").to("cuda")
                logits = self._reranker(**enc).logits.view(-1).float()
                scores += logits.cpu().tolist()
        return scores
