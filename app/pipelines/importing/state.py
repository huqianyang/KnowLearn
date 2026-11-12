"""导入图状态。"""
from pathlib import Path
from typing import TypedDict

from app.parsers.models import Chunk


class ImportState(TypedDict, total=False):
    # 任务信息（由工作线程填入）
    task_id: str
    file_id: str
    file_name: str
    doc_type: str            # course_catalog / question_bank / lecture / project_doc
    course_name: str | None
    project_name: str | None
    upload_path: Path        # 上传后的本地文件
    work_dir: Path           # 本任务的临时目录
    # 节点产出
    file_url: str            # 原文件在 MinIO 的地址
    md_text: str             # Markdown 正文
    md_base_dir: Path        # 解析图片相对路径的基准目录
    chunks: list[Chunk]
    dense: list[list[float]]
    sparse: list[dict[int, float]]
