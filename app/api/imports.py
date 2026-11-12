"""导入接口 A1～A4（API 设计第 3 章）。"""
import asyncio

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from app.core.container import Container, get_container
from app.core.response import ok

router = APIRouter(prefix="/api/v1/imports", tags=["imports"])


@router.get("/suggest")
async def suggest(filename: str = Query(..., min_length=1), doc_type: str = Query(...),
                  c: Container = Depends(get_container)):
    return ok(c.import_service.suggest(filename, doc_type))


@router.post("")
async def create(file: UploadFile = File(...), doc_type: str = Form(...),
                 course_name: str | None = Form(None), project_name: str | None = Form(None),
                 c: Container = Depends(get_container)):
    data = await asyncio.to_thread(c.import_service.create, file, doc_type, course_name, project_name)
    return ok(data, message="已提交导入任务", status_code=202)


@router.get("/{task_id}")
async def get_task(task_id: str, c: Container = Depends(get_container)):
    return ok(await asyncio.to_thread(c.import_service.get, task_id))


@router.get("")
async def list_tasks(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                     status: str | None = Query(None), c: Container = Depends(get_container)):
    return ok(await asyncio.to_thread(c.import_service.list, page, page_size, status))
