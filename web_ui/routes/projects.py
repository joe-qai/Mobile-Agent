"""项目路由"""
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/projects", tags=["projects"])

from backend.db.database import (
    create_project,
    delete_project,
    get_project,
    get_projects_with_stats,
    update_project,
)
from web_ui.models.schemas import BatchDelete, ProjectCreate


@router.get("/")
async def api_get_projects(page: int = 1, size: int = 10):
    """获取项目列表（带聚合统计，支持分页）"""
    result = await asyncio.to_thread(get_projects_with_stats, page, size)
    return result


@router.post("/")
async def api_create_project(data: ProjectCreate):
    """创建项目"""
    project_id = create_project(data.name, data.description)
    return {"id": project_id, "name": data.name, "description": data.description}


@router.post("/batch-delete")
async def api_batch_delete_projects(data: BatchDelete):
    """批量删除项目"""
    deleted = 0
    errors = []
    for pid in data.ids:
        success = delete_project(pid)
        if success:
            deleted += 1
        else:
            errors.append({"id": pid, "error": "Project not found or delete failed"})
    return {"deleted": deleted, "errors": errors}


@router.get("/{project_id}")
async def api_get_project(project_id: int):
    """获取项目详情"""
    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.put("/{project_id}")
async def api_update_project(project_id: int, data: ProjectCreate):
    """更新项目"""
    success = update_project(project_id, data.name, data.description)
    if not success:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"success": True}


@router.delete("/{project_id}")
async def api_delete_project(project_id: int):
    """删除项目"""
    success = delete_project(project_id)
    if not success:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"success": True}
