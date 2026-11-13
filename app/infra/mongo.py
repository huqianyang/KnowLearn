"""MongoDB 客户端与集合初始化（数据库设计第 4 章）。"""
import logging
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

logger = logging.getLogger(__name__)

# 集合名
IMPORT_TASKS = "import_tasks"   # 导入任务（含各步骤状态）
KB_FILES = "kb_files"           # 已入库文件登记（同名覆盖时据此找到旧数据）
SESSIONS = "sessions"           # 会话
MESSAGES = "messages"           # 会话消息


class Mongo:
    """MongoDB 连接与库级初始化操作。

    Args:
        url: 连接串。
        db_name: 数据库名。
    """

    def __init__(self, url: str, db_name: str):
        # tz_aware=True：读出的时间带时区，避免本地时间与 UTC 混用
        self.client = MongoClient(url, serverSelectionTimeoutMS=5000, tz_aware=True)
        self.db: Database = self.client[db_name]

    def ensure_indexes(self) -> None:
        """创建查询所需的索引（已存在时不会重复创建）。"""
        db = self.db
        db[IMPORT_TASKS].create_index([("created_at", DESCENDING)])                          # 任务列表按时间倒序
        db[IMPORT_TASKS].create_index([("status", ASCENDING), ("created_at", DESCENDING)])   # 按状态过滤、领取待执行任务
        db[IMPORT_TASKS].create_index([("file_name", ASCENDING)])
        db[KB_FILES].create_index([("file_name", ASCENDING)], unique=True)                   # 文件名唯一，保证同名覆盖
        db[SESSIONS].create_index([("updated_at", DESCENDING)])                              # 会话列表按最近更新排序
        db[MESSAGES].create_index([("session_id", ASCENDING), ("created_at", ASCENDING)])    # 按会话取消息
        logger.info("Mongo 索引已就绪（库 %s）", db.name)

    def recover_interrupted_tasks(self) -> int:
        """服务重启时，把仍在 pending/running 的导入任务标记为失败；同时复位会话的生成锁。

        Returns:
            被标记为失败的任务数。
        """
        now = datetime.now(timezone.utc)
        res = self.db[IMPORT_TASKS].update_many(
            {"status": {"$in": ["pending", "running"]}},
            {"$set": {"status": "failed", "error": "服务重启，任务中断", "finished_at": now}},
        )
        # 进程退出时正在回答的会话，锁不会被释放，这里统一复位
        self.db[SESSIONS].update_many({"is_generating": True}, {"$set": {"is_generating": False}})
        if res.modified_count:
            logger.warning("已将 %d 个中断的导入任务标记为失败", res.modified_count)
        return res.modified_count

    def ping(self) -> None:
        """连通性检查，失败时抛出异常。"""
        self.client.admin.command("ping")

    def close(self) -> None:
        """关闭连接。"""
        self.client.close()
