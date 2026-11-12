"""M1：统一响应结构、错误码、配置校验。不依赖任何中间件。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.errors import BizError, ErrorCode
from app.core.response import ok, register_exception_handlers


def _client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    class Body(BaseModel):
        query: str

    @app.get("/ok")
    def _ok():
        return ok({"a": 1})

    @app.get("/biz")
    def _biz():
        raise BizError(ErrorCode.SESSION_BUSY)

    @app.post("/validate")
    def _validate(body: Body):
        return ok(body.query)

    @app.get("/boom")
    def _boom():
        raise RuntimeError("secret detail")

    return TestClient(app, raise_server_exceptions=False)


def test_ok_envelope():
    r = _client().get("/ok")
    assert r.status_code == 200
    assert r.json() == {"code": 0, "message": "ok", "data": {"a": 1}}


def test_biz_error_maps_code_and_status():
    r = _client().get("/biz")
    assert r.status_code == 409
    assert r.json()["code"] == 40901 and r.json()["data"] is None


def test_validation_error_is_40003():
    r = _client().post("/validate", json={})
    assert r.status_code == 400 and r.json()["code"] == 40003


def test_unknown_error_hides_detail():
    r = _client().get("/boom")
    assert r.status_code == 500
    assert r.json()["code"] == 50001 and "secret" not in r.json()["message"]


def test_unknown_route_uses_envelope():
    r = _client().get("/nope")
    assert r.status_code == 404 and r.json()["code"] == 40400


def test_settings_missing_required_key_fails(monkeypatch):
    monkeypatch.setenv("SILICONFLOW_API_KEY", "")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]
