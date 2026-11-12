"""集中配置：全部来自 .env，缺少必填项时启动即报错。"""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # ---- 服务 ----
    app_host: str = "127.0.0.1"
    app_port: int = 8010
    log_dir: Path = PROJECT_ROOT / "logs"
    temp_dir: Path = PROJECT_ROOT / "temp_data"
    max_upload_mb: int = 50

    # ---- 硅基流动 ----
    siliconflow_api_key: str = Field(min_length=1)
    siliconflow_api_base: str = "https://api.siliconflow.cn/v1"
    llm_model: str = Field(min_length=1)
    vlm_model: str = Field(min_length=1)

    # ---- 本机模型（只用 GPU） ----
    embedding_model_path: Path
    rerank_model_path: Path

    # ---- 百炼 WebSearch MCP ----
    dashscope_api_key: str = Field(min_length=1)
    web_search_mcp_url: str = Field(min_length=1)

    # ---- MinerU ----
    mineru_api_token: str = Field(min_length=1)
    mineru_api_base: str = "https://mineru.net/api/v4"

    # ---- Milvus ----
    milvus_uri: str = Field(min_length=1)
    milvus_collection: str = "knowlearn_chunks"

    # ---- MongoDB ----
    mongo_url: str = Field(min_length=1)
    mongo_db_name: str = "knowlearn"

    # ---- MinIO ----
    minio_endpoint: str = Field(min_length=1)
    minio_access_key: str = Field(min_length=1)
    minio_secret_key: str = Field(min_length=1)
    minio_bucket: str = "knowlearn"
    minio_secure: bool = False
    minio_public_base: str = Field(min_length=1)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
