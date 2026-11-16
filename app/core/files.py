"""本地临时文件清理。

不用 shutil.rmtree(ignore_errors=True)：Windows 下文件被占用（杀毒扫描、预览、未关闭的句柄）时它只删一半，
并且不留任何记录（审计 A2 实测）。这里记录每个删不掉的文件，并做短暂重试。
"""
import logging
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def remove_tree(path: Path, retries: int = 3, delay: float = 0.5) -> bool:
    """删除目录（不存在时视为成功）。删不干净时重试，最终仍失败则记录残留文件并返回 False，不抛异常。"""
    path = Path(path)
    errors: list[str] = []

    def onexc(func, p, exc):
        """记录删除失败的文件，继续删除其余文件。"""
        errors.append(f"{p}（{type(exc).__name__}: {exc}）")

    for attempt in range(retries):
        if not path.exists():
            return True
        errors.clear()
        shutil.rmtree(path, onexc=onexc)
        if not path.exists():
            return True
        if attempt < retries - 1:
            time.sleep(delay)
    logger.warning("临时目录未能完全删除 %s，残留 %d 项，例如：%s", path, len(errors), errors[:3])
    return False


def sweep_temp_dir(temp_dir: Path, keep: set[str]) -> int:
    """服务启动时清扫临时目录：删除不属于 keep（仍需执行的任务 ID）的子目录，返回清掉的目录数。"""
    temp_dir = Path(temp_dir)
    if not temp_dir.exists():
        return 0
    n = 0
    for child in temp_dir.iterdir():
        if child.name in keep:
            continue
        if child.is_dir():
            n += remove_tree(child)
        else:
            try:
                child.unlink()
                n += 1
            except OSError as e:
                logger.warning("临时文件未能删除 %s：%s", child, e)
    if n:
        logger.info("启动清扫：删除了 %d 个残留的临时目录 / 文件", n)
    return n
