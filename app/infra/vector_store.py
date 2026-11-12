"""Milvus 集合 knowlearn_chunks 的定义与初始化（数据库设计第 3 章）。

注意（技术验证 S5）：集合使用 Strong 一致性，否则刚写入的数据查不到；搜索结果主键字段名为 chunk_id。
"""
import logging

from pymilvus import DataType, MilvusClient

logger = logging.getLogger(__name__)

DENSE_DIM = 1024

# (字段名, 类型, 额外参数)
_SCALAR_FIELDS: list[tuple[str, DataType, dict]] = [
    ("content_type", DataType.VARCHAR, {"max_length": 32}),
    ("content", DataType.VARCHAR, {"max_length": 65535}),
    ("course_name", DataType.VARCHAR, {"max_length": 512, "nullable": True}),
    ("project_name", DataType.VARCHAR, {"max_length": 256, "nullable": True}),
    ("chapter", DataType.VARCHAR, {"max_length": 1024, "nullable": True}),
    ("series_code", DataType.VARCHAR, {"max_length": 128, "nullable": True}),
    ("direction_code", DataType.VARCHAR, {"max_length": 128, "nullable": True}),
    ("bank_name", DataType.VARCHAR, {"max_length": 256, "nullable": True}),
    ("question_code", DataType.VARCHAR, {"max_length": 128, "nullable": True}),
    ("question_type", DataType.VARCHAR, {"max_length": 32, "nullable": True}),
    ("file_name", DataType.VARCHAR, {"max_length": 512}),
    ("file_url", DataType.VARCHAR, {"max_length": 1024}),
    ("images", DataType.JSON, {"nullable": True}),
    ("task_id", DataType.VARCHAR, {"max_length": 64}),
    ("created_at", DataType.INT64, {}),
]
_INVERTED_FIELDS = ["content_type", "question_type", "direction_code", "file_name", "task_id"]
REQUIRED_FIELDS = {"chunk_id", "dense_vector", "sparse_vector"} | {f[0] for f in _SCALAR_FIELDS}
OUTPUT_FIELDS = [f[0] for f in _SCALAR_FIELDS]


class VectorStore:
    def __init__(self, uri: str, collection: str):
        self.client = MilvusClient(uri=uri)
        self.collection = collection

    def ensure_collection(self) -> None:
        if self.client.has_collection(self.collection):
            self._check_compatible()
            self.client.load_collection(self.collection)
            logger.info("Milvus 集合 %s 已存在，结构校验通过", self.collection)
            return
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)
        for name, dtype, kw in _SCALAR_FIELDS:
            schema.add_field(name, dtype, **kw)
        schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=DENSE_DIM)
        schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)

        idx = self.client.prepare_index_params()
        idx.add_index("dense_vector", index_type="HNSW", metric_type="COSINE", params={"M": 16, "efConstruction": 200})
        idx.add_index("sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="IP")
        for f in _INVERTED_FIELDS:
            idx.add_index(f, index_type="INVERTED")
        self.client.create_collection(self.collection, schema=schema, index_params=idx, consistency_level="Strong")
        logger.info("已创建 Milvus 集合 %s", self.collection)

    def _check_compatible(self) -> None:
        desc = self.client.describe_collection(self.collection)
        fields = {f["name"]: f for f in desc["fields"]}
        missing = REQUIRED_FIELDS - fields.keys()
        if missing:
            raise RuntimeError(f"Milvus 集合 {self.collection} 结构不兼容，缺少字段 {sorted(missing)}；"
                               f"请确认集合名配置，不会自动删除已有集合")
        dim = fields["dense_vector"].get("params", {}).get("dim")
        if dim is not None and int(dim) != DENSE_DIM:
            raise RuntimeError(f"Milvus 集合 {self.collection} 向量维度为 {dim}，期望 {DENSE_DIM}")

    def ping(self) -> None:
        self.client.has_collection(self.collection)

    def close(self) -> None:
        self.client.close()
