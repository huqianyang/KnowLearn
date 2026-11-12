"""应用级资源容器：启动时创建一次，各层通过 FastAPI 依赖获取。"""
import logging
from dataclasses import dataclass

from fastapi import Request

from app.core.config import Settings
from app.infra.local_models import LocalModels
from app.infra.mongo import Mongo
from app.infra.storage import Storage
from app.infra.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    mongo: Mongo
    storage: Storage
    vectors: VectorStore
    models: LocalModels

    @classmethod
    def build(cls, s: Settings) -> "Container":
        return cls(
            settings=s,
            mongo=Mongo(s.mongo_url, s.mongo_db_name),
            storage=Storage(s.minio_endpoint, s.minio_access_key, s.minio_secret_key,
                            s.minio_bucket, s.minio_secure, s.minio_public_base),
            vectors=VectorStore(s.milvus_uri, s.milvus_collection),
            models=LocalModels(s.embedding_model_path, s.rerank_model_path),
        )

    def startup(self) -> None:
        """初始化顺序：先连中间件并建好结构，再加载模型。任何一步失败都让启动失败。"""
        self.mongo.ping()
        self.mongo.ensure_indexes()
        self.mongo.recover_interrupted_tasks()
        self.storage.ensure_bucket()
        self.vectors.ensure_collection()
        self.models.load()
        self.settings.temp_dir.mkdir(parents=True, exist_ok=True)

    def shutdown(self) -> None:
        for name, fn in (("mongo", self.mongo.close), ("milvus", self.vectors.close)):
            try:
                fn()
            except Exception:  # noqa: BLE001
                logger.exception("关闭 %s 失败", name)


def get_container(request: Request) -> Container:
    return request.app.state.container
