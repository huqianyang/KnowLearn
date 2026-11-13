"""命令行启动入口：`uv run python -m app`。

从 .env 读取监听地址和端口（默认 127.0.0.1:8010），用 uvicorn 启动 app.main 中的 FastAPI 应用。
"""
import uvicorn

from app.core.config import get_settings

if __name__ == "__main__":
    s = get_settings()
    # 以字符串形式传入应用路径，由 uvicorn 自行导入 app.main:app
    uvicorn.run("app.main:app", host=s.app_host, port=s.app_port)
