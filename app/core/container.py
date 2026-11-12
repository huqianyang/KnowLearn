"""应用级资源容器：启动时创建一次，各层通过 FastAPI 依赖获取。"""
import logging
from dataclasses import dataclass

from fastapi import Request

from app.core.config import Settings
from app.infra.llm import LlmClient
from app.infra.local_models import LocalModels
from app.infra.mineru import MinerUClient
from app.infra.mongo import Mongo
from app.infra.storage import Storage
from app.infra.vector_store import VectorStore
from app.infra.websearch import WebSearchClient
from app.pipelines.importing.graph import Deps, ImportPipeline
from app.pipelines.query.graph import QueryPipeline
from app.services.chat_service import ChatService
from app.services.import_service import ImportService
from app.services.import_worker import ImportWorker
from app.services.session_service import SessionService

logger = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    mongo: Mongo
    storage: Storage
    vectors: VectorStore
    models: LocalModels
    llm: LlmClient
    mineru: MinerUClient
    import_worker: ImportWorker
    import_service: ImportService
    session_service: SessionService
    chat_service: ChatService

    @classmethod
    def build(cls, s: Settings) -> "Container":
        mongo = Mongo(s.mongo_url, s.mongo_db_name)
        storage = Storage(s.minio_endpoint, s.minio_access_key, s.minio_secret_key,
                          s.minio_bucket, s.minio_secure, s.minio_public_base)
        vectors = VectorStore(s.milvus_uri, s.milvus_collection)
        models = LocalModels(s.embedding_model_path, s.rerank_model_path)
        llm = LlmClient(s.siliconflow_api_key, s.siliconflow_api_base, s.llm_model, s.vlm_model)
        mineru = MinerUClient(s.mineru_api_base, s.mineru_api_token)
        pipeline = ImportPipeline(Deps(s, mongo, storage, vectors, models, llm, mineru))
        worker = ImportWorker(mongo, pipeline)
        service = ImportService(s, mongo, on_created=worker.notify)
        sessions = SessionService(mongo)
        web = WebSearchClient(s.web_search_mcp_url, s.dashscope_api_key)
        chat = ChatService(sessions, QueryPipeline(models, vectors, llm, web, s.rerank_min_score), s.history_rounds)
        return cls(s, mongo, storage, vectors, models, llm, mineru, worker, service, sessions, chat)

    def startup(self) -> None:
        """初始化顺序：先连中间件并建好结构，再加载模型，最后启动导入线程。任何一步失败都让启动失败。"""
        self.mongo.ping()
        self.mongo.ensure_indexes()
        self.mongo.recover_interrupted_tasks()
        self.storage.ensure_bucket()
        self.vectors.ensure_collection()
        self.models.load()
        self.settings.temp_dir.mkdir(parents=True, exist_ok=True)
        self.import_worker.start()

    def shutdown(self) -> None:
        for name, fn in (("import_worker", self.import_worker.stop),
                         ("mongo", self.mongo.close), ("milvus", self.vectors.close)):
            try:
                fn()
            except Exception:  # noqa: BLE001
                logger.exception("关闭 %s 失败", name)


def get_container(request: Request) -> Container:
    return request.app.state.container
