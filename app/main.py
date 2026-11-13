"""FastAPI 应用入口（API 决策 D1：单个服务）。

职责：
- 生命周期：启动时创建资源容器（中间件连接、本机模型、导入线程），停止时依次释放；
- 注册统一异常处理和三组路由（健康检查、导入、会话与问答）；
- 托管 static/ 目录下的前端页面，访问根路径时跳转到问答页。

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
STATIC_DIR = PROJECT_ROOT / "static"   # 前端页面所在目录


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：yield 之前是启动逻辑，之后是停止逻辑。

    启动阶段任何一步失败（中间件连不上、模型加载失败等）都会抛出异常，使服务直接启动失败，
    而不是带着残缺的依赖继续运行。
    """
    settings = get_settings()
    setup_logging(settings.log_dir)
    logger.info("KnowLearn 启动中……")
    container = Container.build(settings)
    container.startup()
    # 挂到 app.state 上，路由通过 get_container 依赖取用
    app.state.container = container
    logger.info("KnowLearn 已启动：http://%s:%d/", settings.app_host, settings.app_port)
    try:
        yield
    finally:
        container.shutdown()
        logger.info("KnowLearn 已停止")


def create_app() -> FastAPI:
    """创建并组装 FastAPI 应用：异常处理、API 路由、静态页面。"""
    app = FastAPI(title="KnowLearn 智能教育知识库", version="0.1.0", lifespan=lifespan)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(imports.router)
    app.include_router(sessions.router)

    @app.get("/", include_in_schema=False)
    async def root():
        """根路径默认进入问答页。"""
        return RedirectResponse("/chat.html")

    # 静态文件挂载必须放在 API 路由之后，否则会拦截 /api 开头的请求
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
