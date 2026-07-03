"""任务管理路由"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException

from backend.agent.agent_manager import agent_manager
from backend.db.database import (
    batch_delete_tasks,
    create_task,
    delete_task,
    get_compat_child_tasks,
    get_projects,
    get_script,
    get_scripts_meta,
    get_task,
    get_tasks,
    update_task,
)
from backend.mcp.mcp_tools import mcp_tools
from backend.notification import NotificationEvent, notification_dispatcher
from backend.utils.python_executor import python_executor
from web_ui.models.schemas import BatchDelete, TaskCreate
from web_ui.routes.devices import device_to_response_dict, discover_devices_cached
from web_ui.utils.helpers import invalidate_reports_cache
from web_ui.utils.logger import log_callback

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

STATUS_LABELS = {
    "running": "进行中",
    "finished": "成功",
    "failed": "失败",
}


def _apply_status_label(task: Dict[str, Any]) -> Dict[str, Any]:
    """给任务添加 status_label 字段用于前端显示"""
    task = dict(task)
    task["status_label"] = STATUS_LABELS.get(task.get("status", ""), task.get("status", ""))
    return task


TASK_LIST_OMIT_FIELDS = {"result", "log", "output"}


def _task_list_item(task: Dict[str, Any]) -> Dict[str, Any]:
    compact = {
        key: value
        for key, value in task.items()
        if key not in TASK_LIST_OMIT_FIELDS
    }
    return _apply_status_label(compact)


def _parse_json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _split_device_ids(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not value:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _first_script_id(value: Any, fallback: Any = None):
    if isinstance(value, list) and value:
        return value[0]
    if value:
        return value
    return fallback

def _ensure_agent_realtime_logs() -> None:
    """Keep Agent execution logs connected to the realtime WebSocket queue."""
    agent_manager.set_log_callback(log_callback)


@router.get("/init")
async def api_tasks_init(system_os: Optional[str] = None, project_id: Optional[int] = None):
    """任务管理页面初始化聚合接口 - 一次性返回设备、脚本、项目、任务数据"""

    async def fetch_devices():
        devices = await discover_devices_cached()
        if system_os:
            devices = [d for d in devices if getattr(d, 'system_os', 'Android') == system_os]
        return {
            "devices": [device_to_response_dict(d) for d in devices],
            "current_device": mcp_tools.current_device,
        }

    async def fetch_scripts():
        all_scripts = await asyncio.to_thread(get_scripts_meta, system_os=system_os, project_id=project_id)
        return {"scripts": all_scripts}

    async def fetch_projects():
        all_projects = await asyncio.to_thread(get_projects)
        return {"projects": all_projects}

    async def fetch_tasks():
        tasks = await asyncio.to_thread(get_tasks)
        parent_tasks = []
        normal_tasks = []
        child_map = {}

        for task in tasks:
            task_role = task.get("task_role")
            parent_id = task.get("parent_task_id")

            if task_role == "parent":
                parent_tasks.append(_task_list_item(task))
            elif task_role == "child" and parent_id:
                if parent_id not in child_map:
                    child_map[parent_id] = []
                child_map[parent_id].append(_task_list_item(task))
            else:
                normal_tasks.append(_task_list_item(task))

        for parent in parent_tasks:
            parent["children"] = child_map.get(parent["id"], [])

        result = parent_tasks + normal_tasks
        result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return {"tasks": result}

    devices_result, scripts_result, projects_result, tasks_result = await asyncio.gather(
        fetch_devices(),
        fetch_scripts(),
        fetch_projects(),
        fetch_tasks()
    )

    return {
        "devices": devices_result["devices"],
        "current_device": devices_result["current_device"],
        "scripts": scripts_result["scripts"],
        "projects": projects_result["projects"],
        "tasks": tasks_result["tasks"],
    }


@router.get("/")
async def api_get_tasks(status: Optional[str] = None):
    """获取任务列表（支持父任务和子任务层级结构）"""
    tasks = await asyncio.to_thread(get_tasks, status=status)

    parent_tasks = []
    normal_tasks = []
    child_map = {}

    for task in tasks:
        task_role = task.get("task_role")
        parent_id = task.get("parent_task_id")

        if task_role == "parent":
            parent_tasks.append(_task_list_item(task))
        elif task_role == "child" and parent_id:
            if parent_id not in child_map:
                child_map[parent_id] = []
            child_map[parent_id].append(_task_list_item(task))
        else:
            normal_tasks.append(_task_list_item(task))

    for parent in parent_tasks:
        parent["children"] = child_map.get(parent["id"], [])

    result = parent_tasks + normal_tasks
    result.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    return result


@router.get("/{task_id}")
async def api_get_task(task_id: int):
    """获取任务详情"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return _apply_status_label(task)


@router.post("/")
async def api_create_task(data: TaskCreate):
    """创建任务 - 有script_id走脚本执行，无则走Agent，支持多设备、多脚本，支持UI兼容性测试"""
    script_ids = data.script_ids if data.script_ids else ([data.script_id] if data.script_id else [])

    if not data.project_id:
        raise HTTPException(status_code=422, detail="必须选择所属项目")
    if not script_ids:
        raise HTTPException(status_code=422, detail="必须选择至少一个脚本")
    if not data.device_ids:
        raise HTTPException(status_code=422, detail="必须选择至少一个设备")

    if data.test_type == "ui-compatibility":
        if not script_ids:
            raise HTTPException(status_code=400, detail="兼容性测试需要选择脚本")
        if not data.device_ids:
            raise HTTPException(status_code=400, detail="兼容性测试需要选择设备")

        from backend.compatibility.compatibility_service import compatibility_service

        result = await compatibility_service.create_compat_task(
            script_id=script_ids[0],
            script_ids=script_ids,
            device_ids=data.device_ids,
            platform=data.system_os or "Android",
            remark=data.remark or "",
            project_id=data.project_id,
            compatibility_dimensions=data.compatibility_dimensions or [],
        )

        return {
            "task_ids": [result["parent_task_id"]],
            "count": 1,
            "parent_task_id": result["parent_task_id"],
            "test_type": "ui-compatibility",
            "message": "兼容性测试任务已创建",
        }

    async def execute_task_async(task_id: int):
        task = get_task(task_id)
        if not task:
            return

        try:
            if task.get("script_id"):
                script = get_script(task["script_id"])
                if script:
                    device_id_str = task.get("device_id", "")
                    device_ids = [d.strip() for d in device_id_str.split(",") if d.strip()] if device_id_str else []

                    if device_ids:
                        async def execute_script_async(device_id):
                            return await asyncio.to_thread(python_executor.execute, script["content"], device_id)

                        tasks = [execute_script_async(device_id) for device_id in device_ids]
                        results = await asyncio.gather(*tasks)

                        all_success = all(r.get("success") for r in results)
                        result = {
                            "success": all_success,
                            "device_results": dict(zip(device_ids, results)),
                            "output": "\n".join(r.get("output", "") for r in results),
                            "error": "\n".join(r.get("error", "") for r in results if r.get("error")) or None,
                        }
                    else:
                        result = {"success": False, "error": "No device selected"}
                else:
                    result = {"success": False, "error": "Script not found"}
            else:
                _ensure_agent_realtime_logs()
                result = await agent_manager.execute_task(task.get("remark", ""), task_id, task.get("test_type", "normal"))

            status = "finished" if result.get("success") else "failed"
            completed_at = datetime.now(timezone.utc).isoformat()

            if result.get("success") and task.get("script_id"):
                from backend.utils.script_generator import generate_test_report
                test_report = generate_test_report(
                    task_text=task.get("name", "") or task.get("remark", ""),
                    step_results=[],
                    package_name="",
                    duration=0.0,
                    passed=True,
                )
                result["test_report"] = test_report

            update_task(
                task_id,
                status=status,
                result=json.dumps(result),
                completed_at=completed_at,
            )

            invalidate_reports_cache()
            if result.get("success"):
                test_type = task.get("test_type", "normal")
                if test_type != "ui-compatibility":
                    asyncio.create_task(notification_dispatcher.notify(NotificationEvent(
                        event_type="task_completed",
                        task_name=task.get("name", "未知任务"),
                        status="完成",
                        severity="",
                        completed_at=completed_at,
                        result=str(result),
                        device_id=task.get("device_id", ""),
                        task_type="script" if task.get("script_id") else test_type,
                        role="child",
                        extra={},
                    )))
            elif not result.get("success"):
                asyncio.create_task(notification_dispatcher.notify(NotificationEvent(
                    event_type="task_failed",
                    task_name=task.get("name", "未知任务"),
                    status="失败",
                    severity="",
                    completed_at=completed_at,
                    result=str(result),
                    device_id=task.get("device_id", ""),
                    task_type="script" if task.get("script_id") else task.get("test_type", "normal"),
                    role="child",
                    extra={},
                )))

        except Exception as e:
            status = "failed"
            completed_at = datetime.now(timezone.utc).isoformat()
            result = {"success": False, "error": str(e)}
            update_task(
                task_id,
                status=status,
                result=json.dumps(result),
                completed_at=completed_at,
            )
            invalidate_reports_cache()

    task_ids = []
    for script_id in script_ids:
        task_id = create_task(
            script_id,
            data.remark,
            data.device_ids,
            task_type="script" if script_id else "agent",
            project_id=data.project_id,
            name=data.name,
            test_type=data.test_type or "normal",
        )
        task_ids.append(task_id)
        # 同步设 running，让前端列表立即显示执行中
        update_task(task_id, status="running")
        asyncio.create_task(execute_task_async(task_id))

    if not script_ids:
        task_id = create_task(
            None,
            data.remark,
            data.device_ids,
            task_type="agent",
            project_id=data.project_id,
            name=data.name,
            test_type=data.test_type or "normal",
        )
        task_ids.append(task_id)
        update_task(task_id, status="running")
        asyncio.create_task(execute_task_async(task_id))

    return {"task_ids": task_ids, "count": len(task_ids), "message": "任务已创建并开始执行"}


@router.post("/{task_id}/run")
async def api_run_task(task_id: int):
    """执行任务 — 有脚本走PythonExecutor（支持多设备并行），无脚本走Agent LLM"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    update_task(task_id, status="running")

    try:
        if task.get("script_id"):
            script = get_script(task["script_id"])
            if script:
                device_id_str = task.get("device_id", "")
                device_ids = [d.strip() for d in device_id_str.split(",") if d.strip()] if device_id_str else []

                if device_ids:
                    async def execute_script_async(device_id):
                        return await asyncio.to_thread(python_executor.execute, script["content"], device_id)

                    tasks = [execute_script_async(device_id) for device_id in device_ids]
                    results = await asyncio.gather(*tasks)

                    all_success = all(r.get("success") for r in results)
                    result = {
                        "success": all_success,
                        "device_results": dict(zip(device_ids, results)),
                        "output": "\n".join(r.get("output", "") for r in results),
                        "error": "\n".join(r.get("error", "") for r in results if r.get("error")) or None,
                    }
                else:
                    result = {"success": False, "error": "No device selected"}
            else:
                result = {"success": False, "error": "Script not found"}
        else:
            _ensure_agent_realtime_logs()
            result = await agent_manager.execute_task(task.get("remark", ""), task_id, task.get("test_type", "normal"))

        status = "finished" if result.get("success") else "failed"
        completed_at = datetime.now(timezone.utc).isoformat()
        update_task(
            task_id,
            status=status,
            result=json.dumps(result),
            completed_at=completed_at,
        )

        invalidate_reports_cache()
        if not result.get("success"):
            asyncio.create_task(notification_dispatcher.notify(NotificationEvent(
                event_type="task_failed",
                task_name=task.get("name", "未知任务"),
                status="失败",
                severity="",
                completed_at=completed_at,
                result=str(result),
                device_id=task.get("device_id", ""),
                task_type=task.get("test_type", "normal"),
                role="child",
                extra={},
            )))

        return result
    except Exception as e:
        status = "failed"
        completed_at = datetime.now(timezone.utc).isoformat()
        result = {"success": False, "error": str(e)}
        update_task(
            task_id,
            status=status,
            result=json.dumps(result),
            completed_at=completed_at,
        )
        invalidate_reports_cache()
        asyncio.create_task(notification_dispatcher.notify(NotificationEvent(
            event_type="task_failed",
            task_name=task.get("name", "未知任务"),
            status="失败",
            severity="",
            completed_at=completed_at,
            result=str(result),
            device_id=task.get("device_id", ""),
            task_type=task.get("test_type", "normal"),
            role="child",
            extra={},
        )))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{task_id}/cancel")
async def api_cancel_task(task_id: int):
    """取消任务（支持普通任务和兼容性测试任务）"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.get("status") not in ["running", "pending"]:
        return {"success": False, "message": "任务状态不允许中止"}

    agent_manager.cancel_task()

    if task.get("task_role") == "parent":
        child_tasks = get_compat_child_tasks(task_id)
        for child in child_tasks:
            if child.get("status") in ["running", "pending"]:
                update_task(
                    child["id"],
                    status="cancelled",
                    completed_at=datetime.now(timezone.utc).isoformat()
                )

    update_task(
        task_id,
        status="cancelled",
        completed_at=datetime.now(timezone.utc).isoformat()
    )

    return {"success": True, "message": "任务已成功中止"}


@router.delete("/{task_id}")
async def api_delete_task(task_id: int):
    """删除单个任务（允许删除任何状态的任务）"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    has_report = task.get("status") in ("finished", "failed") and task.get("result")

    success = delete_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")

    return {"success": True, "has_report": has_report, "soft_deleted": has_report}


@router.post("/{task_id}/rerun")
async def api_rerun_task(task_id: int, data: dict = Body(...)):
    """重新执行任务 — 基于原任务创建新任务并立即执行（支持测试类型选择）"""
    old_task = get_task(task_id)
    if not old_task:
        raise HTTPException(status_code=404, detail="Task not found")

    extra = _parse_json_dict(old_task.get("extra"))
    device_ids = _split_device_ids(data.get("device_ids")) or _split_device_ids(
        old_task.get("device_id")
    )

    remark = data.get("remark") or old_task.get("remark", "") or "重新执行任务"
    name = data.get("name") or old_task.get("name")
    test_type = data.get("test_type") or old_task.get("test_type") or (
        "ui-compatibility" if old_task.get("task_role") == "parent" else "normal"
    )
    selected_script_id = _first_script_id(data.get("script_ids"), old_task.get("script_id"))

    if test_type == "ui-compatibility":
        script_id = selected_script_id
        platform = (
            data.get("platform")
            or old_task.get("platform")
            or extra.get("platform")
            or "Android"
        )
        compatibility_dimensions = data.get("compatibility_dimensions")
        if compatibility_dimensions is None:
            compatibility_dimensions = extra.get("compatibility_dimensions", [])
        if not script_id:
            raise HTTPException(status_code=400, detail="兼容性测试需要选择脚本")
        if not device_ids:
            raise HTTPException(status_code=400, detail="兼容性测试需要选择设备")

        from backend.compatibility.compatibility_service import compatibility_service

        result = await compatibility_service.create_compat_task(
            script_id=script_id,
            device_ids=device_ids,
            platform=platform,
            remark=remark,
            project_id=data.get("project_id") or old_task.get("project_id"),
            compatibility_dimensions=compatibility_dimensions,
        )

        # 异步触发执行，不阻塞
        asyncio.create_task(compatibility_service.execute_compat_task(result["parent_task_id"]))

        return {
            "task_id": result["parent_task_id"],
            "count": 1,
            "parent_task_id": result["parent_task_id"],
            "test_type": "ui-compatibility",
            "message": "兼容性测试任务已创建，正在执行",
        }

    new_id = create_task(
        selected_script_id,
        remark,
        device_ids,
        task_type=old_task.get("task_type", "script"),
        project_id=data.get("project_id") or old_task.get("project_id"),
        name=name,
        test_type=test_type,
    )

    async def _execute_task_async(task_id: int):
        task = get_task(task_id)
        if not task:
            return

        if task.get("script_id"):
            script = get_script(task["script_id"])
            if script:
                device_id_str = task.get("device_id", "")
                device_ids = [d.strip() for d in device_id_str.split(",") if d.strip()] if device_id_str else []

                if device_ids:
                    async def execute_script_async(device_id):
                        return await asyncio.to_thread(python_executor.execute, script["content"], device_id)

                    tasks = [execute_script_async(device_id) for device_id in device_ids]
                    results = await asyncio.gather(*tasks)

                    all_success = all(r.get("success") for r in results)
                    result = {
                        "success": all_success,
                        "device_results": dict(zip(device_ids, results)),
                        "output": "\n".join(r.get("output", "") for r in results),
                        "error": "\n".join(r.get("error", "") for r in results if r.get("error")) or None,
                    }
                else:
                    result = {"success": False, "error": "No device selected"}
            else:
                result = {"success": False, "error": "Script not found"}
        else:
            _ensure_agent_realtime_logs()
            result = await agent_manager.execute_task(task.get("remark", ""), task_id, task.get("test_type", "normal"))

        status = "finished" if result.get("success") else "failed"
        update_task(
            task_id,
            status=status,
            result=json.dumps(result),
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        invalidate_reports_cache()

    update_task(new_id, status="running")
    asyncio.create_task(_execute_task_async(new_id))

    return {"task_id": new_id, "count": 1}


@router.post("/batch-delete")
async def api_batch_delete_tasks(data: BatchDelete):
    """批量删除任务（支持删除所有状态的任务）"""
    count = batch_delete_tasks(data.ids)
    return {"success": True, "deleted_count": count}


@router.post("/run-direct")
async def api_run_direct_task(data: TaskCreate):
    """直接执行任务（不持久化）— 有脚本走PythonExecutor，无脚本走Agent LLM"""
    try:
        target_device = data.device_id or (
            data.device_ids[0] if data.device_ids else None
        )
        if target_device:
            await asyncio.to_thread(mcp_tools.select_device, target_device)
        if data.max_steps:
            agent_manager.set_max_steps(data.max_steps)
        if data.script_id:
            script = get_script(data.script_id)
            if script:
                result = await asyncio.to_thread(python_executor.execute, script["content"], target_device)
            else:
                result = {"success": False, "error": "Script not found"}
        else:
            _ensure_agent_realtime_logs()
            result = await agent_manager.execute_task(data.remark)
    except Exception as e:
        result = {"success": False, "error": str(e)}

    return result


@router.post("/cancel-current")
async def api_cancel_current_task():
    """取消当前正在执行的 run-direct 任务（无需 task_id）"""
    cancelled = await asyncio.to_thread(agent_manager.cancel_task)
    if cancelled:
        return {"success": True, "message": "任务已中止"}
    return {"success": False, "message": "当前无正在运行的任务"}
