"""统一响应结构与全局异常处理（API 设计 1.2）。"""
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import BizError, ErrorCode

logger = logging.getLogger(__name__)


def ok(data: Any = None, message: str = "ok", status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"code": 0, "message": message, "data": data})


def fail(error: ErrorCode, message: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=error.http_status,
        content={"code": error.code, "message": message or error.default_message, "data": None},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BizError)
    async def _biz(_: Request, exc: BizError):
        return fail(exc.error, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = "; ".join(f"{'.'.join(str(x) for x in e['loc'][1:])}: {e['msg']}" for e in exc.errors())
        return fail(ErrorCode.INVALID_PARAM, f"参数校验失败：{details}")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code,
                            content={"code": exc.status_code * 100, "message": str(exc.detail), "data": None})

    @app.exception_handler(Exception)
    async def _unknown(request: Request, exc: Exception):
        logger.exception("未处理的异常: %s %s", request.method, request.url.path)
        return fail(ErrorCode.INTERNAL)
