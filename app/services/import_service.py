"""导入服务：参数校验、保存上传文件、创建任务、查询任务（API A1～A4）。"""
import logging
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile
from pymongo import DESCENDING

from app.core.config import Settings
from app.core.errors import BizError, ErrorCode
from app.infra.mongo import IMPORT_TASKS, KB_FILES, Mongo
from app.parsers.filename import suggest_name
from app.pipelines.importing.tracker import initial_steps

logger = logging.getLogger(__name__)

DOC_TYPES = ("course_catalog", "question_bank", "lecture", "project_doc")
ALLOWED_EXT = {
    "course_catalog": {".md"},
    "question_bank": {".md"},
    "lecture": {".md", ".docx", ".pdf", ".zip"},
    "project_doc": {".md", ".docx", ".pdf", ".zip"},
}
TASK_STATUSES = {"pending", "running", "succeeded", "failed"}


class ImportService:
    def __init__(self, settings: Settings, mongo: Mongo, on_created=None):
        self.s = settings
        self.db = mongo.db
        self.on_created = on_created or (lambda: None)

    # ---------- A1 ----------
    def suggest(self, filename: str, doc_type: str) -> dict:
        self._check_doc_type(doc_type)
        return suggest_name(filename, doc_type)

    # ---------- A2 ----------
    def create(self, file: UploadFile, doc_type: str, course_name: str | None, project_name: str | None) -> dict:
        self._check_doc_type(doc_type)
        filename = Path(file.filename or "").name
        ext = Path(filename).suffix.lower()
        if not filename or ext not in ALLOWED_EXT[doc_type]:
            allowed = " / ".join(sorted(ALLOWED_EXT[doc_type]))
            raise BizError(ErrorCode.UNSUPPORTED_FORMAT, f"不支持的文件格式：{ext or '无扩展名'}，该资料类型只支持 {allowed}")
        course_name = (course_name or "").strip() or None
        project_name = (project_name or "").strip() or None
        if doc_type == "lecture" and not course_name:
            raise BizError(ErrorCode.MISSING_METADATA, "课程讲义必须填写课程名")
        if doc_type == "project_doc" and not project_name:
            raise BizError(ErrorCode.MISSING_METADATA, "项目文档必须填写项目名")
        for v in (course_name, project_name):
            if v and len(v) > 100:
                raise BizError(ErrorCode.INVALID_PARAM, "课程名 / 项目名不能超过 100 字")
        if doc_type in ("course_catalog", "question_bank"):
            course_name = project_name = None
        elif doc_type == "lecture":
            project_name = None
        else:
            course_name = None

        task_id = uuid.uuid4().hex
        task_dir = self.s.temp_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        upload_path = task_dir / filename
        try:
            self._save_limited(file, upload_path)
            if ext == ".zip":
                self._check_zip(upload_path)
        except BizError:
            shutil.rmtree(task_dir, ignore_errors=True)
            raise

        existing = self.db[KB_FILES].find_one({"file_name": filename}, {"_id": 1})
        now = datetime.now(timezone.utc)
        doc = {
            "_id": task_id, "file_name": filename, "file_id": existing["_id"] if existing else uuid.uuid4().hex,
            "doc_type": doc_type, "course_name": course_name, "project_name": project_name,
            "status": "pending", "is_overwrite": bool(existing), "steps": initial_steps(),
            "stats": {"chunk_count": None, "image_count": None, "image_failed": None, "image_missing": None},
            "error": None, "upload_path": str(upload_path), "created_at": now, "started_at": None, "finished_at": None,
        }
        self.db[IMPORT_TASKS].insert_one(doc)
        logger.info("[%s] 已创建导入任务 %s（%s）", task_id, filename, doc_type)
        self.on_created()
        return {"task_id": task_id, "file_name": filename, "doc_type": doc_type,
                "status": "pending", "is_overwrite": bool(existing)}

    def _save_limited(self, file: UploadFile, dest: Path) -> None:
        limit, size = self.s.max_upload_bytes, 0
        with open(dest, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise BizError(ErrorCode.FILE_TOO_LARGE, f"文件超过 {self.s.max_upload_mb} MB")
                out.write(chunk)
        if size == 0:
            raise BizError(ErrorCode.INVALID_PARAM, "文件内容为空")

    @staticmethod
    def _check_zip(path: Path) -> None:
        try:
            with zipfile.ZipFile(path) as z:
                mds = [n for n in z.namelist() if n.lower().endswith(".md") and "__MACOSX" not in n]
        except zipfile.BadZipFile:
            raise BizError(ErrorCode.UNSUPPORTED_FORMAT, "压缩包已损坏或不是 zip 格式") from None
        if len(mds) != 1:
            raise BizError(ErrorCode.UNSUPPORTED_FORMAT, f"压缩包内必须有且只有 1 个 .md 文件，实际有 {len(mds)} 个")

    # ---------- A3 ----------
    def get(self, task_id: str) -> dict:
        doc = self.db[IMPORT_TASKS].find_one({"_id": task_id})
        if not doc:
            raise BizError(ErrorCode.TASK_NOT_FOUND)
        return to_task_view(doc, with_steps=True)

    # ---------- A4 ----------
    def list(self, page: int, page_size: int, status: str | None) -> dict:
        q: dict = {}
        if status:
            wanted = {x.strip() for x in status.split(",") if x.strip()}
            if not wanted <= TASK_STATUSES:
                raise BizError(ErrorCode.INVALID_PARAM, f"status 只能是 {sorted(TASK_STATUSES)}")
            q["status"] = {"$in": sorted(wanted)}
        col = self.db[IMPORT_TASKS]
        total = col.count_documents(q)
        docs = col.find(q).sort("created_at", DESCENDING).skip((page - 1) * page_size).limit(page_size)
        return {"total": total, "items": [to_task_view(d, with_steps=False) for d in docs]}

    @staticmethod
    def _check_doc_type(doc_type: str) -> None:
        if doc_type not in DOC_TYPES:
            raise BizError(ErrorCode.INVALID_PARAM, f"doc_type 只能是 {list(DOC_TYPES)}")


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone().isoformat(timespec="seconds") if dt else None


def to_task_view(d: dict, with_steps: bool) -> dict:
    view = {
        "task_id": d["_id"], "file_name": d["file_name"], "doc_type": d["doc_type"],
        "course_name": d.get("course_name"), "project_name": d.get("project_name"),
        "status": d["status"], "is_overwrite": d.get("is_overwrite", False),
        "created_at": _iso(d.get("created_at")), "finished_at": _iso(d.get("finished_at")),
        "duration_ms": int((d["finished_at"] - d["started_at"]).total_seconds() * 1000)
        if d.get("finished_at") and d.get("started_at") else None,
        "error": d.get("error"), "stats": d.get("stats") or {},
    }
    if with_steps:
        view["steps"] = [{"key": s["key"], "name": s["name"], "status": s["status"],
                          "duration_ms": s.get("duration_ms"), "error": s.get("error"),
                          "progress": s.get("progress")} for s in d.get("steps", [])]
    return view
