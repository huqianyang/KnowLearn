"""统一响应结构与全局异常处理（API 设计 1.2）。

所有接口都返回 `{"code": 0, "message": "ok", "data": ...}`；出错时 code 为业务码、data 为 null。
"""
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import BizError, ErrorCode

logger = logging.getLogger(__name__)


def ok(data: Any = None, message: str = "ok", status_code: int = 200) -> JSONResponse:
    """成功响应。status_code 可改为 202 等（例如导入任务已受理）。"""
    return JSONResponse(status_code=status_code, content={"code": 0, "message": message, "data": data})


def fail(error: ErrorCode, message: str | None = None) -> JSONResponse:
    """失败响应：HTTP 状态码和业务码都取自错误码。"""
    return JSONResponse(
        status_code=error.http_status,
        content={"code": error.code, "message": message or error.default_message, "data": None},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理，把各类异常统一转换成上面的响应结构。"""

    @app.exception_handler(BizError)
    async def _biz(_: Request, exc: BizError):
        """业务异常：按其错误码返回。"""
        return fail(exc.error, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        """FastAPI 参数校验失败：拼出「字段: 原因」列表，返回 40003。"""
        # loc 的第一项是参数位置（body / query 等），只保留字段路径
        details = "; ".join(f"{'.'.join(str(x) for x in e['loc'][1:])}: {e['msg']}" for e in exc.errors())
        return fail(ErrorCode.INVALID_PARAM, f"参数校验失败：{details}")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        """框架层 HTTP 异常（如 404 路由不存在、405 方法不允许）：业务码 = 状态码 × 100。"""
        return JSONResponse(status_code=exc.status_code,
                            content={"code": exc.status_code * 100, "message": str(exc.detail), "data": None})

    @app.exception_handler(Exception)
    async def _unknown(request: Request, exc: Exception):
        """兜底：未预料的异常记录完整堆栈，对外只返回 50001，不暴露内部细节。"""
        logger.exception("未处理的异常: %s %s", request.method, request.url.path)
        return fail(ErrorCode.INTERNAL)
