"""MongoDB 客户端与集合初始化（数据库设计第 4 章）。"""
import logging
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

logger = logging.getLogger(__name__)

IMPORT_TASKS = "import_tasks"
KB_FILES = "kb_files"
SESSIONS = "sessions"
MESSAGES = "messages"


class Mongo:
    def __init__(self, url: str, db_name: str):
        self.client = MongoClient(url, serverSelectionTimeoutMS=5000, tz_aware=True)
        self.db: Database = self.client[db_name]

    def ensure_indexes(self) -> None:
        db = self.db
        db[IMPORT_TASKS].create_index([("created_at", DESCENDING)])
        db[IMPORT_TASKS].create_index([("status", ASCENDING), ("created_at", DESCENDING)])
        db[IMPORT_TASKS].create_index([("file_name", ASCENDING)])
        db[KB_FILES].create_index([("file_name", ASCENDING)], unique=True)
        db[SESSIONS].create_index([("updated_at", DESCENDING)])
        db[MESSAGES].create_index([("session_id", ASCENDING), ("created_at", ASCENDING)])
        logger.info("Mongo 索引已就绪（库 %s）", db.name)

    def recover_interrupted_tasks(self) -> int:
        """服务重启时，把仍在 pending/running 的导入任务标记为失败；同时复位会话的生成锁。"""
        now = datetime.now(timezone.utc)
        res = self.db[IMPORT_TASKS].update_many(
            {"status": {"$in": ["pending", "running"]}},
            {"$set": {"status": "failed", "error": "服务重启，任务中断", "finished_at": now}},
        )
        self.db[SESSIONS].update_many({"is_generating": True}, {"$set": {"is_generating": False}})
        if res.modified_count:
            logger.warning("已将 %d 个中断的导入任务标记为失败", res.modified_count)
        return res.modified_count

    def ping(self) -> None:
        self.client.admin.command("ping")

    def close(self) -> None:
        self.client.close()
