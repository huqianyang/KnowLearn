"""日志：控制台 + 按天滚动的 logs/app.log。"""
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if getattr(root, "_knowlearn_configured", False):
        return
    root.setLevel(level)
    fmt = logging.Formatter(FORMAT)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file = TimedRotatingFileHandler(log_dir / "app.log", when="midnight", backupCount=14, encoding="utf-8")
    file.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(file)
    # 第三方库日志降噪
    for noisy in ("httpx", "httpcore", "urllib3", "pymongo", "minio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._knowlearn_configured = True  # type: ignore[attr-defined]
