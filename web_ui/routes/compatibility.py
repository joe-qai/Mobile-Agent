"""
Compatibility test routes.
Provides API endpoints for UI compatibility testing.
"""
import os
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from backend.compatibility.artifact_store import artifact_store
from backend.db.database import get_task_artifacts_by_parent

router = APIRouter(prefix="/api/compat", tags=["compatibility"])

TASK_LIST_OMIT_FIELDS = {"result", "log", "output"}


def _task_list_item(task: dict) -> dict:
    return {
        key: value
        for key, value in task.items()
        if key not in TASK_LIST_OMIT_FIELDS
    }


class BatchIdsRequest(BaseModel):
    ids: List[int]


class ReviewRequest(BaseModel):
    status: str
    remark: str = ""
    reviewed_by: str = ""


class BatchReviewRequest(BaseModel):
    ids: List[int]
    status: str
    remark: str = ""
    reviewed_by: str = ""


@router.post("/tasks")
async def api_create_compat_task(data):
    """Create a compatibility test task."""
    try:
        from backend.compatibility.compatibility_service import compatibility_service

        result = await compatibility_service.create_compat_task(
            script_id=data.script_id,
            task_name=data.task_name,
            device_ids=data.device_ids,
            dimensions=data.dimensions,
            project_id=getattr(data, "project_id", None),
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tasks/{parent_task_id}/execute")
async def api_execute_compat_task(parent_task_id: int):
    """Execute a compatibility task asynchronously."""
    try:
        from backend.db.database import get_compat_parent_task

        parent_task = get_compat_parent_task(parent_task_id)
        if not parent_task:
            raise HTTPException(status_code=404, detail="Task not found")

        import asyncio

        from backend.compatibility.compatibility_service import compatibility_service

        asyncio.create_task(compatibility_service.execute_compat_task(parent_task_id))
        return {"success": True, "message": "Task started"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/{parent_task_id}")
async def api_get_compat_task(parent_task_id: int):
    """Get compatibility task details."""
    try:
        from backend.db.database import get_compat_child_tasks, get_compat_parent_task

        parent_task = get_compat_parent_task(parent_task_id)
        if not parent_task:
            raise HTTPException(status_code=404, detail="Task not found")

        child_tasks = get_compat_child_tasks(parent_task_id)
        return {
            "parent_task": _task_list_item(parent_task),
            "child_tasks": [_task_list_item(task) for task in child_tasks],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/{parent_task_id}/children")
async def api_get_compat_child_tasks(parent_task_id: int):
    """Get child tasks for a compatibility task."""
    try:
        from backend.db.database import get_compat_child_tasks, get_compat_parent_task

        if not get_compat_parent_task(parent_task_id):
            raise HTTPException(status_code=404, detail="Parent task not found")

        children = get_compat_child_tasks(parent_task_id)
        children = [_task_list_item(task) for task in children]
        return {"children": children}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/devices")
async def api_get_compat_devices(platform: Optional[str] = None):
    """Get available device list from the database cache."""
    try:
        from backend.db.database import get_device_list

        devices = get_device_list()
        if platform:
            devices = [d for d in devices if d.get("platform") == platform]
        return {"devices": devices}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/{parent_task_id}/report")
async def api_get_compat_report(parent_task_id: int):
    """Get compatibility test report."""
    try:
        from backend.compatibility.compatibility_service import compatibility_service

        report = await compatibility_service.get_compat_report(parent_task_id)
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/{parent_id}/children/{child_id}/report", response_class=HTMLResponse)
async def api_get_compat_child_report(parent_id: int, child_id: int):
    """Get a child compatibility test report as HTML."""
    try:
        artifacts = get_task_artifacts_by_parent(parent_id)
        child_report = next(
            (
                artifact
                for artifact in artifacts
                if artifact.get("task_id") == child_id
                and artifact.get("artifact_type") in ("compat_child_report", "report")
                and str(artifact.get("relative_path", "")).endswith(".html")
            ),
            None,
        )
        if not child_report:
            raise HTTPException(status_code=404, detail="子设备报告不存在")

        file_path = artifact_store.get_artifact_path(child_report["relative_path"])
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="子设备报告文件不存在")

        with open(file_path, "r", encoding="utf-8") as file:
            return HTMLResponse(content=file.read())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tasks/{parent_task_id}/cancel")
async def api_cancel_compat_task(parent_task_id: int):
    """Cancel a compatibility test task."""
    try:
        from backend.compatibility.compatibility_service import compatibility_service

        compatibility_service.cancel_compat_task(parent_task_id)
        return {"success": True, "message": "Task cancelled"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dimensions")
async def api_get_compat_dimensions():
    """Get compatibility validation dimensions."""
    try:
        from backend.compatibility.assertions import DIMENSION_DESCRIPTIONS

        dimensions = []
        for dim, desc in DIMENSION_DESCRIPTIONS.items():
            dimensions.append(
                {
                    "id": dim,
                    "name": dim.replace("_", " ").title(),
                    "description": desc,
                }
            )
        return {"dimensions": dimensions}
    except ImportError:
        return {
            "dimensions": [
                {"id": "ui_layout", "name": "UI Layout", "description": "UI layout validation"},
                {"id": "text_rendering", "name": "Text Rendering", "description": "Text rendering validation"},
                {"id": "element_presence", "name": "Element Presence", "description": "Element presence validation"},
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/audit/{parent_task_id}")
async def api_get_audit_items(parent_task_id: int):
    """Get compatibility audit items grouped by first_seen_task_id."""
    try:
        from backend.db.database import get_audit_items

        items = get_audit_items(parent_task_id)
        grouped = {}
        for item in items:
            key = item["first_seen_task_id"]
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(item)
        return {"items": items, "grouped": grouped, "parent_task_id": parent_task_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/baselines")
async def api_list_compat_baselines(
    project_id: Optional[int] = None,
    parent_task_id: Optional[int] = None,
    script_id: Optional[int] = None,
    system_os: Optional[str] = None,
    platform: Optional[str] = None,
    severity: Optional[str] = None,
    review_status: Optional[str] = None,
    page: int = 1,
    size: int = 10,
):
    """List screenshot-level compatibility analysis baselines with multi-dimensional filters and pagination."""
    try:
        from backend.db.database import list_compat_analysis_baselines

        result = list_compat_analysis_baselines(
            project_id=project_id,
            parent_task_id=parent_task_id,
            script_id=script_id,
            system_os=system_os,
            platform=platform,
            severity=severity,
            review_status=review_status,
            page=page,
            size=size,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/baselines/{baseline_id}/review")
async def api_review_compat_baseline(baseline_id: int, req: ReviewRequest):
    """Update screenshot-level compatibility analysis baseline review status."""
    try:
        from backend.db.database import update_compat_analysis_baseline_review

        if req.status not in ("pending", "confirmed", "rejected", "fixed"):
            raise HTTPException(status_code=400, detail=f"Invalid status: {req.status}")

        update_compat_analysis_baseline_review(
            baseline_id,
            status=req.status,
            remark=req.remark,
            reviewed_by=req.reviewed_by,
        )
        return {"success": True, "id": baseline_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/baselines/{baseline_id}")
async def api_delete_compat_baseline(baseline_id: int):
    """Delete a screenshot-level compatibility analysis baseline."""
    try:
        from backend.db.database import delete_compat_analysis_baseline

        deleted = delete_compat_analysis_baseline(baseline_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Baseline not found")
        return {"success": True, "id": baseline_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/baselines/batch-delete")
async def api_batch_delete_compat_baselines(req: BatchIdsRequest):
    """Batch delete compatibility analysis baselines."""
    if not req.ids:
        raise HTTPException(status_code=400, detail="ids is required")
    try:
        from backend.db.database import delete_compat_analysis_baseline

        deleted = 0
        errors = []
        for bid in req.ids:
            success = delete_compat_analysis_baseline(bid)
            if success:
                deleted += 1
            else:
                errors.append({"id": bid, "error": "Baseline not found or delete failed"})
        return {"deleted": deleted, "errors": errors}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/baselines/batch-review")
async def api_batch_review_compat_baselines(req: BatchReviewRequest):
    """Batch review compatibility analysis baselines."""
    if not req.ids:
        raise HTTPException(status_code=400, detail="ids is required")
    if req.status not in ("pending", "confirmed", "rejected", "fixed"):
        raise HTTPException(status_code=400, detail=f"Invalid status: {req.status}")
    try:
        from backend.db.database import update_compat_analysis_baseline_review

        reviewed = 0
        errors = []
        for bid in req.ids:
            try:
                update_compat_analysis_baseline_review(bid, status=req.status, remark=req.remark, reviewed_by=req.reviewed_by)
                reviewed += 1
            except Exception:
                errors.append({"id": bid, "error": "Baseline not found or update failed"})
        return {"reviewed": reviewed, "errors": errors}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
async def api_update_audit_item(item_id: int, data: dict):
    """Update audit item status."""
    try:
        from backend.db.database import get_audit_item, update_audit_item

        existing = get_audit_item(item_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Audit item not found")

        status = data.get("status")
        if status not in ("pending", "confirmed", "rejected", "skip"):
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

        update_audit_item(
            item_id,
            status=status,
            remark=data.get("remark", ""),
            reviewed_by=data.get("reviewed_by", ""),
        )
        return {"success": True, "id": item_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/audit/{parent_task_id}/issue-history")
async def api_get_issue_progression(parent_task_id: int):
    """Get issue progression history for a compatibility task."""
    try:
        from backend.db.database import get_issue_progression

        progression = get_issue_progression(parent_task_id)
        return {"progression": progression, "parent_task_id": parent_task_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/audit/{parent_task_id}/issue-history/{issue_type}/{issue_detail}")
async def api_get_issue_history(parent_task_id: int, issue_type: str, issue_detail: str):
    """Get detailed history timeline for one issue."""
    try:
        from urllib.parse import unquote

        from backend.db.database import get_issue_history

        issue_detail_decoded = unquote(issue_detail)
        history = get_issue_history(parent_task_id, issue_type, issue_detail_decoded)
        return {
            "history": history,
            "parent_task_id": parent_task_id,
            "issue_type": issue_type,
            "issue_detail": issue_detail_decoded,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
