"""会话接口 B1～B4（API 设计第 4 章，数据库设计 4.3、4.4）。"""
import uuid
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, ReturnDocument

from app.core.errors import BizError, ErrorCode
from app.infra.mongo import MESSAGES, SESSIONS, Mongo

DEFAULT_TITLE = "新会话"
TITLE_LEN = 20


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone().isoformat(timespec="seconds") if dt else None


def session_view(d: dict) -> dict:
    return {"session_id": d["_id"], "title": d["title"], "created_at": _iso(d["created_at"]),
            "updated_at": _iso(d["updated_at"]), "message_count": d.get("message_count", 0)}


def message_view(d: dict) -> dict:
    v = {"message_id": d["_id"], "role": d["role"], "content": d["content"], "created_at": _iso(d["created_at"])}
    if d["role"] == "assistant":
        v.update(sources=[{k: x for k, x in s.items() if k != "chunk_id"} for s in d.get("sources") or []],
                 images=d.get("images") or [], status=d.get("status"), error=d.get("error"))
    return v


class SessionService:
    def __init__(self, mongo: Mongo):
        self.db = mongo.db

    def create(self) -> dict:
        now = datetime.now(timezone.utc)
        doc = {"_id": uuid.uuid4().hex, "title": DEFAULT_TITLE, "message_count": 0, "is_generating": False,
               "created_at": now, "updated_at": now}
        self.db[SESSIONS].insert_one(doc)
        return session_view(doc)

    def list_sessions(self, page: int, page_size: int) -> dict:
        col = self.db[SESSIONS]
        docs = col.find({}).sort("updated_at", DESCENDING).skip((page - 1) * page_size).limit(page_size)
        return {"total": col.count_documents({}), "items": [session_view(d) for d in docs]}

    def get(self, session_id: str) -> dict:
        d = self.db[SESSIONS].find_one({"_id": session_id})
        if not d:
            raise BizError(ErrorCode.SESSION_NOT_FOUND)
        return d

    def delete(self, session_id: str) -> None:
        res = self.db[SESSIONS].delete_one({"_id": session_id, "is_generating": {"$ne": True}})
        if res.deleted_count == 0:
            self.get(session_id)                       # 不存在 → 40402
            raise BizError(ErrorCode.SESSION_BUSY, "该会话正在回答中，请稍后再删除")
        self.db[MESSAGES].delete_many({"session_id": session_id})

    def messages(self, session_id: str) -> dict:
        self.get(session_id)
        docs = self.db[MESSAGES].find({"session_id": session_id}).sort("created_at", ASCENDING)
        return {"session_id": session_id, "items": [message_view(d) for d in docs]}

    # ---------- 供问答服务使用 ----------

    def acquire(self, session_id: str) -> dict:
        """原子抢占生成锁（API 决策 D4）。"""
        d = self.db[SESSIONS].find_one_and_update({"_id": session_id, "is_generating": {"$ne": True}},
                                                  {"$set": {"is_generating": True}},
                                                  return_document=ReturnDocument.AFTER)
        if d is None:
            self.get(session_id)
            raise BizError(ErrorCode.SESSION_BUSY)
        return d

    def release(self, session_id: str) -> None:
        self.db[SESSIONS].update_one({"_id": session_id}, {"$set": {"is_generating": False}})

    def history(self, session_id: str, rounds: int) -> list[dict]:
        """最近 N 轮 completed 状态的消息（数据库设计 4.4）。"""
        docs = list(self.db[MESSAGES].find({"session_id": session_id, "status": "completed"},
                                           {"role": 1, "content": 1})
                    .sort("created_at", DESCENDING).limit(rounds * 2))
        return [{"role": d["role"], "content": d["content"]} for d in reversed(docs)]

    def add_message(self, doc: dict, first_question: str | None = None) -> None:
        self.db[MESSAGES].insert_one(doc)
        upd: dict = {"$inc": {"message_count": 1}, "$set": {"updated_at": doc["created_at"]}}
        if first_question:
            upd["$set"]["title"] = first_question.strip().replace("\n", " ")[:TITLE_LEN]
        self.db[SESSIONS].update_one({"_id": doc["session_id"]}, upd)
