"""M1：统一响应结构、错误码、配置校验。不依赖任何中间件。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.errors import BizError, ErrorCode
from app.core.response import ok, register_exception_handlers


def _client() -> TestClient:
    """构造一个只注册了全局异常处理的最小应用，每个路由触发一种情况。"""
    app = FastAPI()
    register_exception_handlers(app)

    class Body(BaseModel):
        query: str

    @app.get("/ok")
    def _ok():
        """正常返回。"""
        return ok({"a": 1})

    @app.get("/biz")
    def _biz():
        """抛出业务异常。"""
        raise BizError(ErrorCode.SESSION_BUSY)

    @app.post("/validate")
    def _validate(body: Body):
        """请求体校验。"""
        return ok(body.query)

    @app.get("/boom")
    def _boom():
        """抛出未预料的异常，异常信息不应暴露给调用方。"""
        raise RuntimeError("secret detail")

    # raise_server_exceptions=False：让异常走全局处理并返回响应，而不是直接抛到测试里
    return TestClient(app, raise_server_exceptions=False)


def test_ok_envelope():
    """成功响应为统一结构，code 为 0。"""
    r = _client().get("/ok")
    assert r.status_code == 200
    assert r.json() == {"code": 0, "message": "ok", "data": {"a": 1}}


def test_biz_error_maps_code_and_status():
    """业务异常按错误码返回对应的 HTTP 状态码和业务码。"""
    r = _client().get("/biz")
    assert r.status_code == 409
    assert r.json()["code"] == 40901 and r.json()["data"] is None


def test_validation_error_is_40003():
    """参数校验失败统一返回 400 / 40003。"""
    r = _client().post("/validate", json={})
    assert r.status_code == 400 and r.json()["code"] == 40003


def test_unknown_error_hides_detail():
    """未预料的异常返回 50001，提示中不包含异常细节。"""
    r = _client().get("/boom")
    assert r.status_code == 500
    assert r.json()["code"] == 50001 and "secret" not in r.json()["message"]


def test_unknown_route_uses_envelope():
    """不存在的路由也返回统一结构（业务码 = 404 × 100）。"""
    r = _client().get("/nope")
    assert r.status_code == 404 and r.json()["code"] == 40400


def test_settings_missing_required_key_fails(monkeypatch):
    """必填配置为空时，创建配置对象直接报错（启动即失败）。"""
    monkeypatch.setenv("SILICONFLOW_API_KEY", "")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]
