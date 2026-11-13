"""D1 健康检查：始终返回 HTTP 200，组件异常时 status = degraded（API 设计第 7 章）。"""
import asyncio

from fastapi import APIRouter, Depends

from app.core.container import Container, get_container
from app.core.response import ok

router = APIRouter(prefix="/api/v1", tags=["health"])

CHECK_TIMEOUT = 3.0   # 单个组件检查的超时时间（秒），避免某个中间件卡住导致整个接口无响应


async def _check(fn) -> str:
    """在线程中执行一个同步的 ping 函数，返回 "ok" 或可读的失败原因。"""
    try:
        await asyncio.wait_for(asyncio.to_thread(fn), timeout=CHECK_TIMEOUT)
        return "ok"
    except asyncio.TimeoutError:
        return f"超时（>{CHECK_TIMEOUT:.0f}s）"
    except Exception as e:  # noqa: BLE001
        return f"异常：{str(e)[:120]}"


@router.get("/health")
async def health(c: Container = Depends(get_container)):
    """检查三个中间件和两个本机模型的状态。前端每 30 秒调用一次，用于页面顶部的状态提示。"""
    # 三个中间件并行检查
    milvus, mongodb, minio = await asyncio.gather(
        _check(c.vectors.ping), _check(c.mongo.ping), _check(c.storage.ping))
    components = {
        "milvus": milvus,
        "mongodb": mongodb,
        "minio": minio,
        "embedding_model": "ok" if c.models.embedder_ready else "未加载",
        "rerank_model": "ok" if c.models.reranker_ready else "未加载",
    }
    status = "ok" if all(v == "ok" for v in components.values()) else "degraded"
    return ok({"status": status, "components": components})
