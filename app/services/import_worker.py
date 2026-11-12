"""导入后台工作线程：一次只执行 1 个任务，其余排队（技术方案 T2）。

任务按提交时间先后，用原子更新 pending → running 领取，保证不会被重复执行。
"""
import logging
import threading
from datetime import datetime, timezone

from pymongo import ASCENDING, ReturnDocument

from app.infra.mongo import IMPORT_TASKS, Mongo
from app.pipelines.importing.graph import ImportPipeline

logger = logging.getLogger(__name__)


class ImportWorker:
    def __init__(self, mongo: Mongo, pipeline: ImportPipeline, idle_poll: float = 5.0):
        self.mongo = mongo
        self.pipeline = pipeline
        self.idle_poll = idle_poll
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="import-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()
        logger.info("导入工作线程已启动")

    def notify(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout)

    def _claim(self) -> dict | None:
        return self.mongo.db[IMPORT_TASKS].find_one_and_update(
            {"status": "pending"},
            {"$set": {"status": "running", "started_at": datetime.now(timezone.utc)}},
            sort=[("created_at", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                task = self._claim()
            except Exception:  # noqa: BLE001 —— Mongo 暂时不可用时不能让线程退出
                logger.exception("领取导入任务失败")
                task = None
            if task is None:
                self._wake.wait(self.idle_poll)
                self._wake.clear()
                continue
            logger.info("[%s] 开始导入 %s（%s）", task["_id"], task["file_name"], task["doc_type"])
            try:
                self.pipeline.run(task)
            except Exception:  # noqa: BLE001 —— run() 自身已处理，这里兜底保证线程存活
                logger.exception("[%s] 导入线程未捕获的异常", task["_id"])
