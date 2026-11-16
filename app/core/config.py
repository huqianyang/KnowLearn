"""集中配置：全部来自 .env，缺少必填项时启动即报错。

字段名与环境变量名一一对应（不区分大小写），例如 `siliconflow_api_key` 对应 `SILICONFLOW_API_KEY`。
带 `Field(min_length=1)` 的字段为必填项，没有默认值的路径字段同样必填。
"""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：app/core/config.py 向上两级
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """应用配置。读取项目根目录下的 .env，忽略未声明的变量。"""

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # ---- 服务 ----
    app_host: str = "127.0.0.1"
    app_port: int = 8010
    log_dir: Path = PROJECT_ROOT / "logs"           # 日志目录
    temp_dir: Path = PROJECT_ROOT / "temp_data"     # 导入过程中的临时文件目录
    max_upload_mb: int = 50                         # 单个上传文件的大小上限（MB）
    history_rounds: int = 3            # 多轮对话带入的历史轮数（数据库设计 DB2）
    # 重排相关性阈值（bge-reranker 原始分数），低于它的候选不进入上下文；全部低于它时回答「未找到相关资料」。
    # 2026-11-16 由 -3.0 调到 -2.0：scripts/retrieval_eval.py（21 正例 / 26 负例）中正例最高分最低 -0.51，
    # 无答案题被判有资料 9/26 → 2/26；历史 128 次问答中没有一次「最高分低于 -2.0」，-2～-3 的只是排在末尾的补充资料
    rerank_min_score: float = -2.0
    # 生成答案时是否开启模型的思考模式（改写和 HyDE 固定关闭）。思考内容不展示，但会推迟首字出现。
    # 2026-11-16 对比 15 道评测题：关闭后生成首字中位数 2.1s → 0.6s、生成总时长 5.0s → 3.3s，
    # 答案长度、引用数、正确性无明显差别，因此默认关闭
    answer_thinking: bool = False

    # ---- 硅基流动 ----
    siliconflow_api_key: str = Field(min_length=1)
    siliconflow_api_base: str = "https://api.siliconflow.cn/v1"
    llm_model: str = Field(min_length=1)            # 对话模型：改写、HyDE、答案生成
    vlm_model: str = Field(min_length=1)            # 视觉模型：图片摘要

    # ---- 本机模型（只用 GPU） ----
    embedding_model_path: Path                      # bge-m3 目录
    rerank_model_path: Path                         # bge-reranker-large 目录

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
    minio_secure: bool = False                      # 是否使用 HTTPS 连接 MinIO
    minio_public_base: str = Field(min_length=1)    # 对外访问文件时的地址前缀（拼图片、原文件链接用）

    @property
    def max_upload_bytes(self) -> int:
        """上传大小上限，单位字节。"""
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """返回全局唯一的配置对象（首次调用时读取 .env，之后走缓存）。"""
    return Settings()  # type: ignore[call-arg]
