"""错误码与业务异常（对应 API 设计 1.3）。"""
from enum import Enum


class ErrorCode(Enum):
    """(业务码, HTTP 状态码, 默认提示)"""

    UNSUPPORTED_FORMAT = (40001, 400, "不支持的文件格式，仅支持 .md / .docx / .pdf")
    MISSING_METADATA = (40002, 400, "缺少必填元数据")
    INVALID_PARAM = (40003, 400, "参数校验失败")
    FILE_TOO_LARGE = (40004, 413, "文件超过大小上限")
    TASK_NOT_FOUND = (40401, 404, "导入任务不存在")
    SESSION_NOT_FOUND = (40402, 404, "会话不存在")
    SESSION_BUSY = (40901, 409, "上一个问题还在回答中")
    INTERNAL = (50001, 500, "服务内部错误")
    DEPENDENCY_UNAVAILABLE = (50301, 503, "依赖服务不可用")

    @property
    def code(self) -> int:
        return self.value[0]

    @property
    def http_status(self) -> int:
        return self.value[1]

    @property
    def default_message(self) -> str:
        return self.value[2]


class BizError(Exception):
    def __init__(self, error: ErrorCode, message: str | None = None):
        self.error = error
        self.message = message or error.default_message
        super().__init__(self.message)
