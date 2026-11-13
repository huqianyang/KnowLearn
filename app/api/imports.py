"""导入接口 A1～A4（API 设计第 3 章）。

路由层只做参数接收和响应包装，校验与业务逻辑都在 ImportService 中。
同步的服务方法（读写 MongoDB、保存文件）放到线程中执行，避免阻塞事件循环。
"""
import asyncio

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from app.core.container import Container, get_container
from app.core.response import ok

router = APIRouter(prefix="/api/v1/imports", tags=["imports"])


@router.get("/suggest")
async def suggest(filename: str = Query(..., min_length=1), doc_type: str = Query(...),
                  c: Container = Depends(get_container)):
    """根据文件名给出默认的课程名或项目名，导入页选好文件后调用。"""
    return ok(c.import_service.suggest(filename, doc_type))


@router.post("")
async def create(file: UploadFile = File(...), doc_type: str = Form(...),
                 course_name: str | None = Form(None), project_name: str | None = Form(None),
                 c: Container = Depends(get_container)):
    """上传文件并创建导入任务。任务进入队列后立即返回 202，由后台线程执行。"""
    data = await asyncio.to_thread(c.import_service.create, file, doc_type, course_name, project_name)
    return ok(data, message="已提交导入任务", status_code=202)


@router.get("/{task_id}")
async def get_task(task_id: str, c: Container = Depends(get_container)):
    """查询单个任务的详情（含各步骤状态），导入页轮询使用。"""
    return ok(await asyncio.to_thread(c.import_service.get, task_id))


@router.get("")
async def list_tasks(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                     status: str | None = Query(None), c: Container = Depends(get_container)):
    """分页查询任务列表，可按状态过滤（多个状态用逗号分隔）。"""
    return ok(await asyncio.to_thread(c.import_service.list_tasks, page, page_size, status))
