"""导入图：save_file → convert → images → split → embed → store（技术方案 4.1）。

每个节点开始/结束时更新 import_tasks.steps；任何节点抛出异常都会中止整张图，
由 ImportPipeline.run() 标记失败步骤并清理本任务已写入的数据（数据库设计第 6 章）。
"""
import hashlib
import logging
import mimetypes
import shutil
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from pypdf import PdfReader

from app.core.config import Settings
from app.infra.llm import LlmClient
from app.infra.local_models import LocalModels
from app.infra.mineru import MinerUClient, safe_extract
from app.infra.mongo import IMPORT_TASKS, KB_FILES, Mongo
from app.infra.storage import Storage
from app.infra.vector_store import VectorStore, quote
from app.parsers.course_catalog import LEVELS, course_chunks, parse_course_catalog
from app.parsers.image_refs import context_of, find_image_refs, replace_refs, safe_caption
from app.parsers.markdown_splitter import split_markdown
from app.parsers.models import ParseError
from app.parsers.question_bank import parse_question_bank, question_chunks
from app.pipelines.importing.state import ImportState
from app.pipelines.importing.tracker import StepTracker

logger = logging.getLogger(__name__)

PDF_MAX_PAGES = 200            # MinerU 单文件上限（技术方案 T7）
IMAGE_WORKERS = 4              # 视觉模型并发数（技术方案 4.2）
EMBED_BATCH = 16               # 每批向量化的切片数（每批结束后更新一次进度）


class ImportFailed(RuntimeError):
    """可预期的失败，message 直接展示给运营。"""


@dataclass
class Deps:
    """导入图用到的全部外部依赖。"""

    settings: Settings
    mongo: Mongo
    storage: Storage
    vectors: VectorStore
    models: LocalModels
    llm: LlmClient
    mineru: MinerUClient


def object_prefix(file_id: str, task_id: str) -> str:
    """本任务在 MinIO 中的目录前缀。按任务分目录，覆盖导入或失败清理时可以整体删除。"""
    return f"files/{file_id}/{task_id}/"


class ImportPipeline:
    """导入流程编排：用 LangGraph 把 6 个节点串成一条直线。

    同一时刻只有一个任务在执行（由 ImportWorker 保证），因此 tracker 可以作为实例属性。
    """

    def __init__(self, deps: Deps):
        self.d = deps
        g = StateGraph(ImportState)
        # 节点名与 tracker 中的步骤 key 一致，方法名为 _node_<节点名>
        for name in ("save_file", "convert", "images", "split", "embed", "store"):
            g.add_node(name, getattr(self, f"_node_{name}"))
        g.add_edge(START, "save_file")
        g.add_edge("save_file", "convert")
        g.add_edge("convert", "images")
        g.add_edge("images", "split")
        g.add_edge("split", "embed")
        g.add_edge("embed", "store")
        g.add_edge("store", END)
        self.graph = g.compile()
        self.tracker: StepTracker | None = None

    # ---------------- 入口 ----------------

    def run(self, task: dict) -> None:
        """执行一个导入任务（task 为 import_tasks 中的文档），结束时任务状态为 succeeded 或 failed。

        本方法不向外抛异常：失败信息写入任务记录，并清理本任务产生的数据和临时文件。
        """
        tasks = self.d.mongo.db[IMPORT_TASKS]
        task_id = task["_id"]
        self.tracker = StepTracker(self.d.mongo.db, task_id)
        work_dir = self.d.settings.temp_dir / task_id
        state: ImportState = {
            "task_id": task_id, "file_id": task["file_id"], "file_name": task["file_name"],
            "doc_type": task["doc_type"], "course_name": task.get("course_name"),
            "project_name": task.get("project_name"), "upload_path": Path(task["upload_path"]),
            "work_dir": work_dir,
        }
        t0 = time.time()
        try:
            self.graph.invoke(state)
            tasks.update_one({"_id": task_id}, {"$set": {"status": "succeeded", "finished_at": _now()}})
            logger.info("[%s] 导入成功：%s，用时 %.0fs", task_id, task["file_name"], time.time() - t0)
        except Exception as e:  # noqa: BLE001
            # 可预期的失败直接展示原因；其他异常附上异常类型，并记录完整堆栈便于排查
            msg = str(e) if isinstance(e, (ImportFailed, ParseError)) else f"{type(e).__name__}: {e}"
            if not isinstance(e, (ImportFailed, ParseError)):
                logger.exception("[%s] 导入异常", task_id)
            else:
                logger.warning("[%s] 导入失败：%s", task_id, msg)
            self.tracker.fail(msg)
            tasks.update_one({"_id": task_id}, {"$set": {"status": "failed", "error": msg, "finished_at": _now()}})
            self._cleanup_failed(task_id, task["file_id"])
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _cleanup_failed(self, task_id: str, file_id: str) -> None:
        """失败时删除本任务已写入的数据，旧版本保持不变。"""
        try:
            self.d.vectors.delete(f"task_id == {quote(task_id)}")
            self.d.storage.delete_prefix(object_prefix(file_id, task_id))
        except Exception:  # noqa: BLE001
            logger.exception("[%s] 失败清理出错", task_id)

    # ---------------- 节点 ----------------
    # 每个节点接收当前状态，返回需要合并进状态的字段

    def _node_save_file(self, s: ImportState) -> dict:
        """把上传的原文件存入 MinIO，得到原文件地址（来源卡片中「打开原文件」使用）。"""
        t = self.tracker
        t.start("save_file")
        key = object_prefix(s["file_id"], s["task_id"]) + "raw/" + s["file_name"]
        url = self.d.storage.upload_file(key, s["upload_path"])
        t.done("save_file")
        return {"file_url": url}

    def _node_convert(self, s: ImportState) -> dict:
        """得到 Markdown 正文。

        - .md：直接读取，跳过本步骤；
        - .zip：解压，要求恰好包含 1 个 .md（图片放在同一压缩包内）；
        - .docx / .pdf：调用 MinerU 转换，PDF 先检查页数。
        转换结果同时存入 MinIO 备查。
        """
        t = self.tracker
        path = s["upload_path"]
        ext = path.suffix.lower()
        if ext == ".md":
            t.skip("convert")
            return {"md_text": _read_text(path), "md_base_dir": path.parent}
        t.start("convert")
        work = s["work_dir"]
        if ext == ".zip":
            out = work / "unzip"
            with zipfile.ZipFile(path) as z:
                safe_extract(z, out)
            # 忽略 macOS 打包时附带的 __MACOSX 目录
            mds = [p for p in out.rglob("*.md") if "__MACOSX" not in p.parts]
            if len(mds) != 1:
                raise ImportFailed(f"压缩包内必须有且只有 1 个 .md 文件，实际有 {len(mds)} 个")
            md_path = mds[0]
        else:
            if ext == ".pdf":
                pages = len(PdfReader(str(path)).pages)
                if pages > PDF_MAX_PAGES:
                    raise ImportFailed(f"PDF 共 {pages} 页，超过 {PDF_MAX_PAGES} 页，请拆分后上传")
            md_path = self.d.mineru.convert(path, work / "mineru",
                                            on_progress=lambda c, n: t.progress("convert", c, n))
        text = _read_text(md_path)
        key = object_prefix(s["file_id"], s["task_id"]) + f"markdown/{Path(s['file_name']).stem}.md"
        self.d.storage.upload_file(key, md_path)
        t.done("convert")
        # 图片的相对路径以 Markdown 所在目录为基准
        return {"md_text": text, "md_base_dir": md_path.parent}

    def _node_images(self, s: ImportState) -> dict:
        """处理讲义和项目文档中的图片：上传 MinIO，调用视觉模型生成摘要，把引用改写为 ![摘要](公开地址)。

        课程目录和题库不处理图片。单张图片失败用「图片」占位，找不到的图片删除引用，都不会让任务失败。
        """
        t = self.tracker
        md = s["md_text"]
        refs = find_image_refs(md) if s["doc_type"] in ("lecture", "project_doc") else []
        if not refs:
            t.skip("images")
            return {}
        t.start("images")
        base = s["md_base_dir"].resolve()
        prefix = object_prefix(s["file_id"], s["task_id"]) + "images/"

        # 1. 解析每个引用指向的图片；本地图片按内容哈希去重（S1：每份讲义都有的 Logo 只调用一次）
        jobs: dict[str, dict] = {}            # 去重键 → {data|url, mime, ref}
        ref_key: dict[int, str | None] = {}   # ref.start → 去重键（None = 图片缺失）
        missing = 0
        for r in refs:
            if r.src.startswith(("http://", "https://")):
                # 网络图片：不下载，直接把地址交给视觉模型，地址本身作为去重键
                key = r.src
                jobs.setdefault(key, {"remote": r.src, "mime": "", "ref": r})
            else:
                # 本地图片：去掉 ?query 和 #hash，且不允许指向 Markdown 目录之外（防路径穿越）
                p = (base / r.src.split("?")[0].split("#")[0]).resolve()
                if not p.is_relative_to(base) or not p.is_file():
                    ref_key[r.start] = None
                    missing += 1
                    continue
                data = p.read_bytes()
                key = hashlib.sha256(data).hexdigest()
                if key not in jobs:
                    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
                    jobs[key] = {"path": p, "data": data, "mime": mime, "ref": r}
            ref_key[r.start] = key

        total, done_n, failed = len(jobs), 0, 0
        t.progress("images", 0, total, force=True)

        def work(key: str) -> tuple[str, str, str, bool]:
            """处理一张去重后的图片，返回 (去重键, 公开地址, 摘要, 是否成功)。在线程池中执行。"""
            job = jobs[key]
            # 上下文取该图片第一次出现的位置
            before, after = context_of(md, job["ref"])
            if "remote" in job:
                url, image = job["remote"], job["remote"]
            else:
                ext = job["path"].suffix.lower() or ".jpg"
                url = self.d.storage.upload_file(f"{prefix}{key[:16]}{ext}", job["path"])
                image = job["data"]
            try:
                return key, url, safe_caption(self.d.llm.describe_image(image, job["mime"], before, after)), True
            except Exception as e:  # noqa: BLE001 —— 技术方案 T5：单张失败用占位符继续
                logger.warning("[%s] 图片摘要失败 %s：%s", s["task_id"], key[:16], e)
                return key, url, "图片", False

        results: dict[str, tuple[str, str]] = {}   # 去重键 → (公开地址, 摘要)
        with ThreadPoolExecutor(max_workers=IMAGE_WORKERS) as pool:
            # 按完成顺序统计进度，避免某张慢图卡住进度条
            for fut in as_completed([pool.submit(work, k) for k in jobs]):
                key, url, caption, ok = fut.result()
                results[key] = (url, caption)
                done_n += 1
                failed += 0 if ok else 1
                t.progress("images", done_n, total)

        # 2. 改写引用：同一张图片的多处引用共用一个结果；缺失的图片删除引用
        replacements = {start: (None if key is None else f"![{results[key][1]}]({results[key][0]})")
                        for start, key in ref_key.items()}
        t.stats(image_count=total, image_failed=failed, image_missing=missing)
        t.done("images")
        return {"md_text": replace_refs(md, replacements)}

    def _node_split(self, s: ImportState) -> dict:
        """按资料类型解析和切分：课程目录每个系列一片，题库每道题一片，讲义和项目文档按章节切分。"""
        t = self.tracker
        t.start("split")
        dt, md = s["doc_type"], s["md_text"]
        if dt == "course_catalog":
            chunks = course_chunks(parse_course_catalog(md))
        elif dt == "question_bank":
            chunks = question_chunks(parse_question_bank(md, self._direction_series_from_kb()))
        else:
            ctype = "doc_chunk" if dt == "lecture" else "project_material"
            chunks = split_markdown(md, ctype, course_name=s.get("course_name"), project_name=s.get("project_name"))
        if not chunks:
            raise ImportFailed("没有解析出任何内容，请检查文件")
        t.stats(chunk_count=len(chunks))
        t.done("split")
        return {"chunks": chunks}

    def _direction_series_from_kb(self) -> dict[str, list[str]]:
        """从知识库中已导入的课程介绍取「方向 → 3 个系列名」，供题库确定课程归属（技术方案 T6）。"""
        rows = self.d.vectors.query('content_type == "course_intro"', ["course_name", "series_code", "direction_code"])
        order = {lv: i for i, lv in enumerate(LEVELS)}
        out: dict[str, list[tuple[int, str]]] = {}
        for r in rows:
            # 系列编码最后一段是级别，按 入门 → 项目 → 强化 排序；未知级别排最后
            level = r["series_code"].rpartition("_")[2]
            out.setdefault(r["direction_code"], []).append((order.get(level, 9), r["course_name"]))
        return {d: [n for _, n in sorted(v)] for d, v in out.items()}

    def _node_embed(self, s: ImportState) -> dict:
        """分批计算每个切片的稠密向量和稀疏向量。"""
        t = self.tracker
        t.start("embed")
        texts = [c.embed_text for c in s["chunks"]]
        dense: list[list[float]] = []
        sparse: list[dict[int, float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            r = self.d.models.encode(texts[i:i + EMBED_BATCH])
            dense += r.dense
            sparse += r.sparse
            t.progress("embed", min(i + EMBED_BATCH, len(texts)), len(texts))
        t.done("embed")
        return {"dense": dense, "sparse": sparse}

    def _node_store(self, s: ImportState) -> dict:
        """写入 Milvus，成功后切换为生效版本（覆盖导入时删除旧版本）。"""
        t = self.tracker
        t.start("store")
        now_ms = int(time.time() * 1000)
        rows = []
        for c, dv, sv in zip(s["chunks"], s["dense"], s["sparse"]):
            rows.append({
                "chunk_id": uuid.uuid4().hex, "content_type": c.content_type, "content": c.content,
                "course_name": c.course_name, "project_name": c.project_name, "chapter": c.chapter,
                "series_code": c.series_code, "direction_code": c.direction_code, "bank_name": c.bank_name,
                "question_code": c.question_code, "question_type": c.question_type,
                "file_name": s["file_name"], "file_url": s["file_url"],
                "images": [{"url": i.url, "caption": i.caption} for i in c.images] or None,
                "task_id": s["task_id"], "created_at": now_ms,
                "dense_vector": dv, "sparse_vector": sv or {0: 1e-6},   # Milvus 不接受空稀疏向量
            })
        self.d.vectors.insert(rows)
        self._activate(s, len(rows))
        t.done("store")
        return {}

    def _activate(self, s: ImportState, chunk_count: int) -> None:
        """新版本写入成功后切换生效版本，再删除旧版本（数据库设计第 6 章）。"""
        db = self.d.mongo.db
        old = db[KB_FILES].find_one({"file_name": s["file_name"]})
        now = _now()
        # 文件登记：同名文件只有一条记录，指向当前生效的任务
        db[KB_FILES].update_one(
            {"file_name": s["file_name"]},
            {"$set": {"doc_type": s["doc_type"], "course_name": s.get("course_name"),
                      "project_name": s.get("project_name"), "file_url": s["file_url"],
                      "active_task_id": s["task_id"], "chunk_count": chunk_count, "updated_at": now},
             "$setOnInsert": {"_id": s["file_id"], "created_at": now}},
            upsert=True,
        )
        # 删除同名文件其他任务写入的切片（即旧版本）
        removed = self.d.vectors.delete(f"file_name == {quote(s['file_name'])} and task_id != {quote(s['task_id'])}")
        # 删除旧版本在 MinIO 中的文件；旧任务的 file_id 取自其任务记录，取不到时用登记记录的 _id
        if old and old.get("active_task_id") and old["active_task_id"] != s["task_id"]:
            old_task = db[IMPORT_TASKS].find_one({"_id": old["active_task_id"]}, {"file_id": 1}) or {}
            self.d.storage.delete_prefix(object_prefix(old_task.get("file_id", old["_id"]), old["active_task_id"]))
        if removed:
            logger.info("[%s] 覆盖导入：删除旧版本 %d 条", s["task_id"], removed)


def _now() -> datetime:
    """当前 UTC 时间。"""
    return datetime.now(timezone.utc)


def _read_text(p: Path) -> str:
    """读取文本文件：优先 UTF-8（兼容带 BOM），失败再按 GB18030（兼容 GBK）读取。"""
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ImportFailed(f"无法识别文件编码：{p.name}（请使用 UTF-8）")
