"""FastAPI 应用入口（API 决策 D1：单个服务）。

启动：uv run python -m app      （端口取自 .env 的 APP_PORT，默认 8010）
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import health, imports, sessions
from app.core.config import PROJECT_ROOT, get_settings
from app.core.container import Container
from app.core.logging import setup_logging
from app.core.response import register_exception_handlers

logger = logging.getLogger("app")
STATIC_DIR = PROJECT_ROOT / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_dir)
    logger.info("KnowLearn 启动中……")
    container = Container.build(settings)
    container.startup()
    app.state.container = container
    logger.info("KnowLearn 已启动：http://%s:%d/", settings.app_host, settings.app_port)
    try:
        yield
    finally:
        container.shutdown()
        logger.info("KnowLearn 已停止")


def create_app() -> FastAPI:
    app = FastAPI(title="KnowLearn 智能教育知识库", version="0.1.0", lifespan=lifespan)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(imports.router)
    app.include_router(sessions.router)

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/chat.html")

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
