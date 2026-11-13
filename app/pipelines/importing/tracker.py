"""导入任务步骤状态记录（API A3 / 数据库设计 4.1 的 steps 字段）。"""
import time
from datetime import datetime, timezone

from pymongo.database import Database

from app.infra.mongo import IMPORT_TASKS

# 导入的 6 个步骤：(key, 页面显示名)，顺序即执行顺序
STEPS: list[tuple[str, str]] = [
    ("save_file", "保存原文件"),
    ("convert", "转换为 Markdown"),
    ("images", "图片处理"),
    ("split", "解析与切分"),
    ("embed", "向量化"),
    ("store", "写入知识库"),
]


def initial_steps() -> list[dict]:
    """新建任务时的步骤列表，全部为 waiting（等待中）。"""
    return [{"key": k, "name": n, "status": "waiting", "started_at": None, "finished_at": None,
             "duration_ms": None, "error": None, "progress": None} for k, n in STEPS]


def _now() -> datetime:
    """当前 UTC 时间。"""
    return datetime.now(timezone.utc)


class StepTracker:
    """把单个任务的步骤状态写入 MongoDB，导入页轮询任务详情即可看到实时进度。

    步骤状态：waiting → running → done / skipped / failed。
    """

    def __init__(self, db: Database, task_id: str):
        self.col = db[IMPORT_TASKS]
        self.task_id = task_id
        self.current: str | None = None   # 正在执行的步骤，失败时用来定位
        self._t0: float = 0.0             # 当前步骤的开始时间
        self._last_progress = 0.0         # 上次写进度的时间

    def _set(self, key: str, fields: dict) -> None:
        """更新 steps 数组中 key 对应的那一项（$ 定位符指向匹配到的数组元素）。"""
        self.col.update_one({"_id": self.task_id, "steps.key": key},
                            {"$set": {f"steps.$.{k}": v for k, v in fields.items()}})

    def start(self, key: str) -> None:
        """步骤开始。"""
        self.current, self._t0 = key, time.time()
        self._set(key, {"status": "running", "started_at": _now()})

    def done(self, key: str) -> None:
        """步骤完成，记录耗时。"""
        self._set(key, {"status": "done", "finished_at": _now(), "duration_ms": int((time.time() - self._t0) * 1000)})
        self.current = None

    def skip(self, key: str) -> None:
        """步骤不适用（如 .md 文件不需要转换），标记为跳过。"""
        self._set(key, {"status": "skipped", "finished_at": _now()})
        self.current = None

    def fail(self, error: str) -> None:
        """把正在执行的步骤标记为失败；没有正在执行的步骤时不做任何事。"""
        if self.current:
            self._set(self.current, {"status": "failed", "finished_at": _now(), "error": error,
                                     "duration_ms": int((time.time() - self._t0) * 1000)})

    def progress(self, key: str, current: int, total: int, force: bool = False) -> None:
        """更新步骤进度（如 图片 26/99）。force=True 或已到最后一项时一定写入。"""
        # 限频：每秒最多写一次，避免频繁写库
        if force or time.time() - self._last_progress >= 1.0 or current >= total:
            self._last_progress = time.time()
            self._set(key, {"progress": {"current": current, "total": total}})

    def stats(self, **kv) -> None:
        """写入任务统计（切片数、图片数、图片失败数等）。"""
        self.col.update_one({"_id": self.task_id}, {"$set": {f"stats.{k}": v for k, v in kv.items()}})
