"""报告管理路由"""

import asyncio
import io
import json
import os
import re
import time
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

router = APIRouter(prefix="/api/reports", tags=["reports"])

from backend.db.database import (
    batch_delete_reports,
    delete_child_task,
    format_device_display_name,
    get_all_tasks,
    get_child_tasks_by_parent,
    get_reports_page,
    get_reports_stats,
    get_task,
    get_task_artifacts_by_parent,
    get_task_events,
)
from backend.mcp.mcp_tools import mcp_tools
from web_ui.utils.helpers import get_reports_cache, set_reports_cache

_device_display_name_cache: Dict[str, str] = {}


def _get_device_display_name(device_id: str) -> str:
    """将设备ID转换为品牌型号+系统版本"""
    if not device_id:
        return ""
    if device_id in _device_display_name_cache:
        return _device_display_name_cache[device_id]
    try:
        dev_info = mcp_tools.get_device_info(device_id)
        if dev_info:
            if isinstance(dev_info, dict):
                brand = dev_info.get("brand", "")
                model = dev_info.get("model", "")
            else:
                brand = getattr(dev_info, "brand", "")
                model = getattr(dev_info, "model", "")
            display_name = format_device_display_name(
                brand,
                model,
            )
            _device_display_name_cache[device_id] = display_name
            return display_name
    except:
        pass
    _device_display_name_cache[device_id] = format_device_display_name()
    return _device_display_name_cache[device_id]


REPORT_STATUS_LABELS = {
    "finished": "成功",
    "failed": "失败",
    "partial_failed": "部分失败",
    "running": "运行中",
    "pending": "待执行",
    "cancelled": "已取消",
}

STEP_STATUS_LABELS = {
    "passed": "通过",
    "failed": "失败",
    "warning": "告警",
    "running": "执行中",
    "unknown": "未知",
}

COMPAT_SEVERITY_LABELS = {
    "blocker": "阻塞",
    "major": "严重",
    "minor": "警告",
    "suggestion": "建议",
}

COMPAT_ASSESSMENT_LABELS = {
    "fail": "阻塞",
    "failed": "阻塞",
    "warning": "警告",
    "warn": "警告",
    "pass": "建议",
    "passed": "建议",
    "unknown": "未知",
}


TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "web_ui",
    "templates",
)


def _get_compat_report_artifacts(parent_task_id: int) -> dict:
    artifacts = get_task_artifacts_by_parent(parent_task_id)
    parent_report = None
    child_reports = {}
    for artifact in artifacts:
        artifact_type = artifact.get("artifact_type")
        if artifact_type == "compat_parent_report":
            parent_report = artifact
        elif artifact_type == "compat_child_report":
            child_reports[artifact.get("task_id")] = artifact
    return {"parent": parent_report, "children": child_reports}


def _build_compat_child_report_items(
    parent_task_id: int, children: list[dict], child_reports: dict
) -> list[dict]:
    items = []
    for child in children:
        report = child_reports.get(child["id"])
        items.append(
            {
                "child_id": child["id"],
                "device_id": child.get("device_id", ""),
                "device_display_name": child.get("device_display_name")
                or _get_device_display_name(child.get("device_id", "")),
                "status": child.get("status", ""),
                "report_path": report.get("relative_path") if report else "",
                "report_artifact_type": report.get("artifact_type") if report else "",
                "report_url": f"/api/compat/tasks/{parent_task_id}/children/{child['id']}/report"
                if report
                else "",
            }
        )
    return items


@router.get("/")
async def api_get_reports(
    page: Optional[int] = Query(None),
    size: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    project: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    """获取报告列表（支持分页和筛选）"""
    now = int(time.time())

    if page is not None:
        safe_size = size or 10
        items, total = await asyncio.to_thread(
            get_reports_page,
            page=page,
            size=safe_size,
            status=status,
            project_id=project,
            date_from=date_from,
            date_to=date_to,
        )
        reports = []
        for task in items:
            test_type = (
                "ui-compatibility" if task.get("task_role") == "parent" else "normal"
            )
            children = []
            report_artifacts = {"parent": None, "children": {}}
            if task.get("task_role") == "parent":
                children = get_child_tasks_by_parent(task["id"])
                report_artifacts = _get_compat_report_artifacts(task["id"])
            reports.append(
                {
                    "task_id": task["id"],
                    "name": task.get("name", ""),
                    "remark": task.get("remark", ""),
                    "status": task["status"],
                    "created_at": task["created_at"],
                    "completed_at": task["completed_at"],
                    "project_name": task.get("project_name", ""),
                    "test_type": test_type,
                    "report_path": report_artifacts["parent"].get("relative_path")
                    if report_artifacts["parent"]
                    else "",
                    "report_artifact_type": report_artifacts["parent"].get(
                        "artifact_type"
                    )
                    if report_artifacts["parent"]
                    else "",
                    "children": _build_compat_child_report_items(
                        task["id"], children, report_artifacts["children"]
                    ),
                }
            )
        stats = await asyncio.to_thread(get_reports_stats)
        return {
            "items": reports,
            "total": total,
            "page": page,
            "size": safe_size,
            "stats": stats,
        }

    _reports_cache, _reports_cache_time, _reports_cache_ttl = get_reports_cache()
    if _reports_cache and (now - _reports_cache_time) < _reports_cache_ttl:
        return _reports_cache

    tasks = await asyncio.to_thread(get_all_tasks)
    reports = []
    for task in tasks:
        if task["status"] in ["finished", "failed", "partial_failed"]:
            if task.get("task_role") == "child":
                continue

            test_type = (
                "ui-compatibility" if task.get("task_role") == "parent" else "normal"
            )
            children = []
            report_artifacts = {"parent": None, "children": {}}
            if task.get("task_role") == "parent":
                children = get_child_tasks_by_parent(task["id"])
                report_artifacts = _get_compat_report_artifacts(task["id"])
            reports.append(
                {
                    "task_id": task["id"],
                    "name": task.get("name", ""),
                    "remark": task.get("remark", ""),
                    "status": task["status"],
                    "created_at": task["created_at"],
                    "completed_at": task["completed_at"],
                    "project_name": task.get("project_name", ""),
                    "test_type": test_type,
                    "report_path": report_artifacts["parent"].get("relative_path")
                    if report_artifacts["parent"]
                    else "",
                    "report_artifact_type": report_artifacts["parent"].get(
                        "artifact_type"
                    )
                    if report_artifacts["parent"]
                    else "",
                    "children": _build_compat_child_report_items(
                        task["id"], children, report_artifacts["children"]
                    ),
                }
            )

    reports.sort(key=lambda x: x.get("completed_at") or "", reverse=True)

    set_reports_cache(reports, now)
    return reports


@router.get("/{task_id}")
async def api_get_report(task_id: int):
    """获取单个报告详情"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/export")
async def api_export_report_removed():
    raise HTTPException(
        status_code=404,
        detail="JSON export has been removed. Use /api/reports/batch-export instead.",
    )


@router.get("/screenshot/{event_id}")
async def api_get_screenshot(event_id: int):
    """获取断言截图（懒加载）"""
    events = get_task_events(event_id)
    if not events:
        raise HTTPException(status_code=404, detail="Event not found")

    event = events[0]
    evidence = event.get("evidence", {})
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except json.JSONDecodeError:
            raise HTTPException(status_code=404, detail="Invalid evidence format")

    screenshot_base64 = evidence.get("annotated_screenshot_base64") or evidence.get(
        "screenshot_base64"
    )
    if not screenshot_base64:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    import base64

    try:
        png_bytes = base64.b64decode(screenshot_base64)
    except Exception:
        raise HTTPException(status_code=404, detail="Invalid base64 data")

    return Response(content=png_bytes, media_type="image/png")


def _escape_html(text: Optional[str]) -> str:
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_datetime(dt_str: Optional[str]) -> str:
    if not dt_str:
        return "-"
    try:
        raw_value = str(dt_str).strip()
        iso_value = raw_value.replace(" ", "T")
        if iso_value.endswith("Z"):
            iso_value = f"{iso_value[:-1]}+00:00"

        parsed = datetime.fromisoformat(iso_value)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        elif " " in raw_value and "T" not in raw_value:
            parsed = parsed.replace(tzinfo=timezone.utc).astimezone()

        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return dt_str[:19] if len(dt_str) >= 19 else dt_str


def _format_duration(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return _escape_html(value)
    if seconds < 1:
        return f"{seconds:.2f}s"
    if seconds < 10:
        return f"{seconds:.1f}s"
    return f"{seconds:.0f}s"


def _status_tone(status: str) -> str:
    if status in ("finished", "passed"):
        return "success"
    if status == "failed":
        return "danger"
    if status in ("partial_failed", "warning"):
        return "warning"
    if status in ("running", "pending"):
        return "warning"
    return "muted"


def _task_status_label(status: str) -> str:
    return REPORT_STATUS_LABELS.get(status, status or "未知")


def _compat_task_status_label(status: str) -> str:
    labels = {
        "finished": "完成",
        "failed": "阻塞",
        "partial_failed": "部分异常",
        "running": "运行中",
        "pending": "待执行",
        "cancelled": "已取消",
    }
    return labels.get(status, status or "未知")


def _step_status_label(status: str) -> str:
    return STEP_STATUS_LABELS.get(status, status or "未知")


def _report_title(task: Dict[str, Any]) -> str:
    parts = []
    if task.get("project_name"):
        parts.append(task["project_name"])
    if task.get("name"):
        parts.append(task["name"])
    if task.get("remark"):
        parts.append(task["remark"])
    if not parts:
        parts.append(f"任务 #{task.get('id')}")
    return " - ".join(parts)


def _screenshot_src(screenshot: Any) -> str:
    if not screenshot:
        return ""
    raw_value = str(screenshot).strip()
    data_uri_pattern = r"^data:image/(png|jpeg|jpg|webp);base64,[A-Za-z0-9+/=\s]+$"
    if re.fullmatch(data_uri_pattern, raw_value, re.IGNORECASE):
        return re.sub(r"\s+", "", raw_value)

    compact_value = re.sub(r"\s+", "", raw_value)
    if re.fullmatch(r"[A-Za-z0-9+/=]+", compact_value):
        return f"data:image/png;base64,{compact_value}"

    if isinstance(screenshot, dict):
        return screenshot.get("src") or screenshot.get("url") or ""

    if raw_value.startswith("compat/"):
        return f"/artifacts/{raw_value}"

    if raw_value.startswith("/"):
        return raw_value

    if "." in raw_value and not raw_value.isspace():
        return f"/artifacts/{raw_value}"

    return ""


def _normalize_evidence(evidence: Any) -> Dict[str, Any]:
    if isinstance(evidence, dict):
        return evidence
    if isinstance(evidence, str) and evidence.strip():
        try:
            parsed = json.loads(evidence)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_vlm_assertion_steps(
    output: str, device_id: str = None
) -> List[Dict[str, Any]]:
    steps = []
    display_device_id = _get_device_display_name(device_id)
    device_prefix = f"[{display_device_id}] " if device_id else ""
    for line in output.split("\n"):
        line = line.strip()
        if line.startswith('{"type": "assertion"'):
            try:
                event = json.loads(line)
                if event.get("type") == "assertion":
                    name = event.get("name", "UI检查点")
                    dimension = event.get("dimension", "unknown")
                    status = event.get("status", "passed")
                    message = event.get("message", "")
                    step_index = event.get("step_index", 0)
                    evidence = _normalize_evidence(event.get("evidence"))
                    vlm_analysis = (
                        evidence.get("vlm_analysis")
                        if isinstance(evidence.get("vlm_analysis"), dict)
                        else {}
                    )
                    issues = (
                        vlm_analysis.get("issues", [])
                        if isinstance(vlm_analysis.get("issues", []), list)
                        else []
                    )
                    issue_lines = []
                    for issue in issues:
                        if not isinstance(issue, dict):
                            continue
                        description = issue.get("description", "")
                        severity = issue.get("severity", "")
                        severity_label = COMPAT_SEVERITY_LABELS.get(
                            str(severity).lower(), severity
                        )
                        location = issue.get("location", "")
                        suggestion = issue.get("suggestion", "")
                        line_parts = [
                            part
                            for part in [
                                f"[{severity_label}]" if severity_label else "",
                                description,
                                f"位置: {location}" if location else "",
                                f"建议: {suggestion}" if suggestion else "",
                            ]
                            if part
                        ]
                        if line_parts:
                            issue_lines.append(" ".join(line_parts))
                    assessment = vlm_analysis.get("overall_assessment")
                    confidence = vlm_analysis.get("confidence")
                    analysis_line = ""
                    has_vlm_assessment = bool(
                        re.search(r"VLM\s*评估", message, re.IGNORECASE)
                    )
                    if assessment and not has_vlm_assessment:
                        assessment_cn = COMPAT_ASSESSMENT_LABELS.get(
                            assessment.lower(), assessment
                        )
                        analysis_line = f"\nVLM评估: {assessment_cn}"
                        if isinstance(confidence, (int, float)):
                            analysis_line += f" / 置信度: {round(confidence * 100)}%"
                    issues_text = (
                        ("\n问题明细:\n" + "\n".join(issue_lines))
                        if issue_lines
                        else ""
                    )
                    screenshot = (
                        evidence.get("annotated_screenshot_base64")
                        or evidence.get("screenshot_base64")
                        or evidence.get("screenshot")
                    )
                    step = {
                        "action": f"{device_prefix}{name}",
                        "status": status,
                        "log": (
                            f"设备: {display_device_id}\n维度: {dimension}\n{message}{analysis_line}{issues_text}"
                            if device_id
                            else f"维度: {dimension}\n{message}{analysis_line}{issues_text}"
                        ),
                        "index": step_index,
                        "dimension": dimension,
                    }
                    analysis_state = evidence.get("analysis_state")
                    baseline_id = evidence.get("baseline_id")
                    baseline_status = evidence.get("baseline_review_status")
                    baseline_remark = evidence.get("baseline_review_remark")
                    if analysis_state:
                        step["analysis_state"] = analysis_state
                    if baseline_id:
                        step["baseline_id"] = baseline_id
                    if baseline_status:
                        step["baseline_review_status"] = baseline_status
                    if baseline_remark:
                        step["baseline_review_remark"] = baseline_remark
                    if screenshot:
                        step["screenshot"] = screenshot
                    if vlm_analysis:
                        step["vlm_analysis"] = vlm_analysis
                    if issues:
                        step["issues"] = issues
                    steps.append(step)
            except json.JSONDecodeError:
                continue
    return steps


def _parse_script_output_to_steps(
    result_data: Dict[str, Any], device_id: str = None
) -> List[Dict[str, Any]]:
    output = result_data.get("output", "")
    error = result_data.get("error", "")
    success = result_data.get("success", False)

    steps = _parse_vlm_assertion_steps(output, device_id)

    display_device_id = _get_device_display_name(device_id)
    device_prefix = f"[{display_device_id}] " if device_id else ""

    if "test session starts" in output:
        test_pattern = r"(PASSED|FAILED|ERROR)\s+.*::(test_\w+)"
        matches = re.findall(test_pattern, output)

        if matches:
            for status, test_name in matches:
                steps.append(
                    {
                        "action": f"{device_prefix}{test_name.replace('_', ' ').capitalize()}",
                        "status": "passed" if status == "PASSED" else "failed",
                        "log": f"设备: {display_device_id}\n测试结果: {status}\n{output[:500]}"
                        if device_id
                        else f"测试结果: {status}\n{output[:500]}",
                    }
                )
        else:
            error_pattern = r"ERROR\s+at\s+(setup|teardown|test)\s+of\s+(.*)"
            error_match = re.search(error_pattern, output)
            if error_match:
                error_type, test_name = error_match.groups()
                steps.append(
                    {
                        "action": f"{device_prefix}{test_name.replace('_', ' ').capitalize()}",
                        "status": "failed",
                        "log": f"设备: {display_device_id}\n{error_type} 错误\n{output[:500]}"
                        if device_id
                        else f"{error_type} 错误\n{output[:500]}",
                    }
                )

    if not steps:
        log_content = output[:1000] if output else (error[:1000] if error else "")
        if device_id:
            log_content = f"设备: {display_device_id}\n{log_content}"

        steps.append(
            {
                "action": f"{device_prefix}脚本执行",
                "status": "passed" if success else "failed",
                "log": log_content,
            }
        )

    return steps


def _parse_report_result(task: Dict[str, Any]) -> Dict[str, Any]:
    result = task.get("result", "{}")
    try:
        result_data = (
            json.loads(result) if isinstance(result, str) and result else result
        )
    except json.JSONDecodeError:
        result_data = {"message": result}
    if not isinstance(result_data, dict):
        result_data = {"message": str(result_data)}

    if "device_results" in result_data:
        device_results = result_data.get("device_results", [])
        all_steps = []

        if isinstance(device_results, list):
            for device_result in device_results:
                if isinstance(device_result, dict):
                    device_id = device_result.get("device_id", "unknown")
                    device_steps = _parse_script_output_to_steps(
                        device_result, device_id
                    )
                    all_steps.extend(device_steps)
        elif isinstance(device_results, dict):
            for device_id, device_result in device_results.items():
                if isinstance(device_result, dict):
                    device_steps = _parse_script_output_to_steps(
                        device_result, device_id
                    )
                    all_steps.extend(device_steps)

        result_data["steps"] = all_steps

        passed_count = sum(1 for step in all_steps if step.get("status") == "passed")
        failed_count = sum(1 for step in all_steps if step.get("status") == "failed")
        result_data["summary"] = {
            "total": len(all_steps),
            "passed": passed_count,
            "failed": failed_count,
            "skipped": 0,
        }
    elif "output" in result_data and "returncode" in result_data:
        steps = _parse_script_output_to_steps(result_data)
        result_data["steps"] = steps

        passed_count = sum(1 for step in steps if step.get("status") == "passed")
        failed_count = sum(1 for step in steps if step.get("status") == "failed")
        result_data["summary"] = {
            "total": len(steps),
            "passed": passed_count,
            "failed": failed_count,
            "skipped": 0,
        }

    if task.get("task_role") == "parent" and not result_data.get("steps"):
        try:
            from backend.db.database import get_compat_child_tasks

            child_steps = []
            for child in get_compat_child_tasks(task["id"]):
                child_result = child.get("result", "{}")
                try:
                    child_result_data = (
                        json.loads(child_result)
                        if isinstance(child_result, str) and child_result
                        else child_result
                    )
                except json.JSONDecodeError:
                    child_result_data = {}
                if not isinstance(child_result_data, dict):
                    child_result_data = {}
                child_result_data.setdefault(
                    "success", child.get("status") == "finished"
                )
                child_result_data["output"] = child.get("log") or child_result_data.get(
                    "output", ""
                )
                child_result_data.setdefault(
                    "error", child_result_data.get("error", "")
                )
                child_steps.extend(
                    _parse_script_output_to_steps(
                        child_result_data, child.get("device_id")
                    )
                )
            if child_steps:
                result_data["steps"] = child_steps
                passed_count = sum(
                    1 for step in child_steps if step.get("status") == "passed"
                )
                failed_count = sum(
                    1 for step in child_steps if step.get("status") == "failed"
                )
                result_data["summary"] = {
                    "total": len(child_steps),
                    "passed": passed_count,
                    "failed": failed_count,
                    "skipped": 0,
                }
        except Exception:
            pass

    return result_data


def _normalize_report_steps(result_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_steps = result_data.get("steps", [])
    if not isinstance(raw_steps, list):
        return []

    steps = []
    for index, raw_step in enumerate(raw_steps, start=1):
        if isinstance(raw_step, dict):
            step = dict(raw_step)
        else:
            step = {"action": str(raw_step)}

        if not step.get("status"):
            if step.get("success") is True:
                step["status"] = "passed"
            elif step.get("success") is False:
                step["status"] = "failed"
            else:
                step["status"] = "unknown"

        if step.get("status") == "unknown":
            step["status"] = "warning"
            log = step.get("log", "")
            if "人工审核" not in log:
                step["log"] = (
                    f"{log}\nUI兼容性分析异常，需要人工审核"
                    if log
                    else "UI兼容性分析异常，需要人工审核"
                )

        step.setdefault("action", f"步骤 {index}")
        step.setdefault("log", "")
        steps.append(step)
    return steps


def _is_ui_checkpoint(step: Dict[str, Any]) -> bool:
    log = step.get("log", "") or step.get("message", "") or ""
    return (
        bool(re.search(r"VLM\s*(评估|分析)", log, re.IGNORECASE))
        or "兼容性" in log
        or "UI检查" in log
        or "视觉评估" in log
    )


def _extract_step_evidence(step: Dict[str, Any]) -> Dict[str, Any]:
    evidence = step.get("evidence") or {}
    if isinstance(evidence, str):
        try:
            parsed = json.loads(evidence)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return evidence if isinstance(evidence, dict) else {}


def _count_ui_severities(steps: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"blocker": 0, "major": 0, "minor": 0, "suggestion": 0}
    for step in steps:
        evidence = _extract_step_evidence(step)
        vlm_analysis = (
            evidence.get("vlm_analysis") if isinstance(evidence, dict) else None
        )
        if not vlm_analysis:
            vlm_analysis = step.get("vlm_analysis")
        issues = (
            vlm_analysis.get("issues", []) if isinstance(vlm_analysis, dict) else []
        )
        if not isinstance(issues, list):
            issues = []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            severity = str(issue.get("severity") or "").lower()
            if severity in counts:
                counts[severity] += 1
    return counts


def _normalize_report_summary(
    result_data: Dict[str, Any],
    steps: List[Dict[str, Any]],
    is_ui_compat: bool = False,
) -> Dict[str, int]:
    if is_ui_compat:
        counted_steps = [step for step in steps if _is_ui_checkpoint(step)]
        total = len(counted_steps) or len(steps)
        script_failed = sum(
            1
            for step in steps
            if step.get("status") == "failed" and not _is_ui_checkpoint(step)
        )
    else:
        counted_steps = steps
        total = len(steps)
        script_failed = 0

    passed_count = sum(1 for step in counted_steps if step.get("status") == "passed")
    failed_count = sum(1 for step in counted_steps if step.get("status") == "failed")
    warning_count = sum(1 for step in counted_steps if step.get("status") == "warning")

    severity_counts = _count_ui_severities(counted_steps if is_ui_compat else [])
    major_count = severity_counts["major"]

    return {
        "total": total,
        "passed": passed_count,
        "failed": failed_count,
        "major": major_count,
        "warning": warning_count,
        "effective_total": passed_count + failed_count + major_count + warning_count
        if (passed_count + failed_count + major_count + warning_count) > 0
        else len(steps),
        "script_failed": script_failed,
    }


def _get_vlm_tag_class(vlm_result: str) -> str:
    vlm_lower = vlm_result.lower().strip()
    if vlm_lower in ("pass", "passed", "success", "true"):
        return "pass"
    elif vlm_lower in ("fail", "failed", "error", "false"):
        return "fail"
    elif vlm_lower in ("warning", "warn", "partial"):
        return "warning"
    return "unknown"


def _get_confidence_tag_class(confidence: str) -> str:
    try:
        val = int(confidence.strip().replace("%", ""))
        if val == 0:
            return "zero"
        elif val < 50:
            return "low"
        elif val < 80:
            return "medium"
        else:
            return "high"
    except (ValueError, TypeError):
        return "zero"


def _highlight_vlm_result(log_text: str) -> str:
    if not log_text:
        return ""

    log_text = _escape_html(log_text).replace("\n", "<br>")

    vlm_pattern = (
        r"(VLM评估[：:]?\s*)(pass|fail|success|error|warning|passed|failed|true|false)"
    )

    def replace_vlm(match):
        prefix = match.group(1)
        result = match.group(2)
        tag_class = _get_vlm_tag_class(result)
        return f'{prefix}<span class="vlm-tag {tag_class}">{result}</span>'

    log_text = re.sub(vlm_pattern, replace_vlm, log_text, flags=re.IGNORECASE)

    confidence_pattern = r"(置信度[：:]?\s*)(\d+)%"

    def replace_confidence(match):
        prefix = match.group(1)
        value = match.group(2)
        tag_class = _get_confidence_tag_class(value)
        return f'{prefix}<span class="confidence-tag {tag_class}">{value}%</span>'

    log_text = re.sub(confidence_pattern, replace_confidence, log_text)

    return log_text


def _render_baseline_badges(step: Dict[str, Any]) -> str:
    state = step.get("analysis_state")
    if state == "new":
        return '<span class="baseline-badge baseline-badge-new">New</span>'
    if state == "reused":
        parts = ['<span class="baseline-badge baseline-badge-reused">Reused</span>']
        status = step.get("baseline_review_status")
        remark = step.get("baseline_review_remark")
        if status:
            parts.append(
                f'<span class="baseline-badge baseline-badge-reused">{_escape_html(status)}</span>'
            )
        if remark:
            parts.append(
                f'<span class="baseline-badge baseline-badge-reused">{_escape_html(remark)}</span>'
            )
        return "".join(parts)
    return ""


def _render_baseline_actions(step: Dict[str, Any]) -> str:
    baseline_id = step.get("baseline_id")
    if not baseline_id or step.get("baseline_review_status") != "pending":
        return ""

    safe_baseline_id = _escape_html(str(baseline_id))
    return (
        f'<div class="baseline-actions" data-baseline-review="{safe_baseline_id}">'
        '<button type="button" class="baseline-action" data-review-action="confirmed">确认问题</button>'
        '<button type="button" class="baseline-action" data-review-action="expected">符合预期</button>'
        '<button type="button" class="baseline-action" data-review-action="skip">忽略</button>'
        '<button type="button" class="baseline-action danger" data-review-action="delete">删除</button>'
        "</div>"
    )


def _checkpoint_status_label(step: Dict[str, Any]) -> str:
    evidence = _extract_step_evidence(step)
    vlm_analysis = evidence.get("vlm_analysis") if isinstance(evidence, dict) else None
    if not isinstance(vlm_analysis, dict):
        vlm_analysis = step.get("vlm_analysis")
    issues = (
        vlm_analysis.get("issues", [])
        if isinstance(vlm_analysis, dict)
        else step.get("issues", [])
    )
    if not isinstance(issues, list):
        issues = []
    label_map = {
        "blocker": "阻塞",
        "major": "严重",
        "minor": "警告",
        "warning": "警告",
        "suggestion": "建议",
    }
    for severity in ("blocker", "major", "minor", "warning", "suggestion"):
        if any(
            str(issue.get("severity") or "").lower() == severity
            for issue in issues
            if isinstance(issue, dict)
        ):
            return label_map[severity]
    return _step_status_label(str(step.get("status") or "unknown"))


def _build_report_context(task: Dict[str, Any]) -> Dict[str, Any]:
    result_data = _parse_report_result(task)
    steps = _normalize_report_steps(result_data)
    raw_test_type = str(task.get("test_type") or "").lower()
    is_ui_compat = (
        raw_test_type in {"ui-compatibility", "ui_compatibility", "compat"}
        or task.get("task_role") == "parent"
    )
    summary = _normalize_report_summary(result_data, steps, is_ui_compat)

    effective_total = summary.get("effective_total", summary["total"])
    pass_rate = (
        round((summary["passed"] / effective_total) * 100) if effective_total else 0
    )
    status = task.get("status", "unknown")
    warning_count = summary.get("warning", 0)
    failed_count = summary.get("failed", 0)
    passed_count = summary.get("passed", 0)
    major_count = summary.get("major", 0)
    total = summary["total"]
    script_failed = summary.get("script_failed", 0)

    if not is_ui_compat:
        if status == "finished" and failed_count == 0:
            report_summary = f"普通测试为线性脚本：1 个测试用例，执行成功，共 {len(steps)} 条执行记录。"
        else:
            report_summary = f"普通测试为线性脚本：1 个测试用例，执行失败，请查看 {len(steps)} 条执行记录中的错误信息。"
    elif warning_count > 0 and failed_count == 0:
        status = "finished"
        report_summary = (
            f"共 {total} 个UI检查点，{passed_count} 个建议，"
            f"{major_count} 个严重，{warning_count} 个警告（UI兼容性警告不影响任务状态）"
        )
    elif script_failed > 0 and failed_count == 0:
        report_summary = (
            f"共 {total} 个UI检查点，{passed_count} 个建议，"
            f"{major_count} 个严重，{warning_count} 个警告（{script_failed} 个脚本执行异常，与兼容性无关）"
        )
    elif failed_count > 0:
        report_summary = (
            f"共 {total} 个UI检查点，{passed_count} 个建议，"
            f"{failed_count} 个阻塞，{major_count} 个严重，{warning_count} 个警告"
        )
    elif warning_count > 0:
        report_summary = (
            f"共 {total} 个UI检查点，{passed_count} 个建议，"
            f"{major_count} 个严重，{warning_count} 个警告"
        )
    else:
        report_summary = f"共 {total} 个UI检查点，{passed_count} 个建议"

    return {
        "task_id": task["id"],
        "title": _report_title(task),
        "remark": task.get("remark", ""),
        "status": status,
        "status_label": _compat_task_status_label(status)
        if is_ui_compat
        else _task_status_label(status),
        "status_tone": _status_tone(status),
        "created_at": _format_datetime(task.get("created_at")),
        "completed_at": _format_datetime(task.get("completed_at")),
        "project_name": task.get("project_name", "") or "-",
        "script_name": task.get("script_name", "") or "-",
        "test_type": "UI兼容性测试" if is_ui_compat else "普通测试",
        "is_ui_compat": is_ui_compat,
        "steps_title": "UI检查点" if is_ui_compat else "脚本执行记录",
        "steps_count_label": f"共 {len(steps)} 个检查点"
        if is_ui_compat
        else f"共 {len(steps)} 条记录",
        "message": result_data.get("message") or result_data.get("result") or "",
        "summary": summary,
        "pass_rate": max(0, min(100, pass_rate)),
        "steps": steps,
        "report_summary": report_summary,
    }


def _render_summary_cards(report: Dict[str, Any]) -> str:
    summary = report.get("summary", {})
    total = summary.get("total", 0)
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    warning = summary.get("warning", 0)
    is_ui_compat = (
        bool(report.get("is_ui_compat")) or report.get("test_type") == "UI兼容性测试"
    )

    if not is_ui_compat:
        is_success = report.get("status_tone") == "success"
        result_label = "成功" if is_success else "失败"
        result_class = "result-success" if is_success else "result-fail"
        return "\n".join(
            [
                '<div class="stat-card">',
                '<div class="stat-icon total"><i class="fa fa-file-text-o"></i></div>',
                '<div class="stat-label">测试用例</div>',
                '<div class="stat-value">1</div>',
                "</div>",
                '<div class="stat-card">',
                f'<div class="stat-icon {"pass" if is_success else "fail"}"><i class="fa fa-{"check-circle" if is_success else "times-circle"}"></i></div>',
                '<div class="stat-label">执行结果</div>',
                f'<div class="stat-value {result_class}">{result_label}</div>',
                "</div>",
                '<div class="stat-card">',
                '<div class="stat-icon total"><i class="fa fa-list-ol"></i></div>',
                '<div class="stat-label">执行记录</div>',
                f'<div class="stat-value">{len(report.get("steps", []))}</div>',
                "</div>",
            ]
        )

    icons = {
        "total": "fa-tasks",
        "pass": "fa-check-circle",
        "fail": "fa-times-circle",
        "warning": "fa-exclamation-circle",
    }
    labels = {
        "pass": "建议" if is_ui_compat else "通过",
        "fail": "阻塞" if is_ui_compat else "失败",
        "warning": "警告" if is_ui_compat else "告警",
    }

    major = summary.get("major", 0)

    cards_html = []
    cards_html.append(
        f'<div class="stat-card">'
        f'<div class="stat-icon total"><i class="fa {icons["total"]}"></i></div>'
        f'<div class="stat-label">检查点数量</div>'
        f'<div class="stat-value">{total}</div>'
        f"</div>"
    )
    cards_html.append(
        f'<div class="stat-card">'
        f'<div class="stat-icon pass"><i class="fa {icons["pass"]}"></i></div>'
        f'<div class="stat-label">{labels["pass"]}</div>'
        f'<div class="stat-value">{passed}</div>'
        f"</div>"
    )
    cards_html.append(
        f'<div class="stat-card">'
        f'<div class="stat-icon fail"><i class="fa {icons["fail"]}"></i></div>'
        f'<div class="stat-label">{labels["fail"]}</div>'
        f'<div class="stat-value">{failed}</div>'
        f"</div>"
    )
    if is_ui_compat:
        cards_html.append(
            f'<div class="stat-card">'
            f'<div class="stat-icon major"><i class="fa fa-exclamation-triangle"></i></div>'
            f'<div class="stat-label">严重</div>'
            f'<div class="stat-value">{major}</div>'
            f"</div>"
        )
    cards_html.append(
        f'<div class="stat-card">'
        f'<div class="stat-icon warning"><i class="fa {icons["warning"]}"></i></div>'
        f'<div class="stat-label">{labels["warning"]}</div>'
        f'<div class="stat-value">{warning}</div>'
        f"</div>"
    )
    return "\n".join(cards_html)


def _render_report_steps(report: Dict[str, Any]) -> str:
    if not report["steps"]:
        message = report["message"] or "暂无步骤记录"
        return f"""
        <div style="text-align:center; padding:40px; color:#9ca3af;">
            <i class="fa fa-file-text-o" style="font-size:48px; margin-bottom:16px; opacity:0.5;"></i>
            <div style="font-size:16px; color:#6b7280;">暂无步骤记录</div>
            <div style="font-size:13px; margin-top:8px;">{_escape_html(message)}</div>
        </div>
        """

    is_normal_report = report.get("test_type") == "普通测试"
    parts = []
    for index, step in enumerate(report["steps"], start=1):
        status = str(step.get("status") or "unknown")
        tone = _status_tone(status)
        action = step.get("action") or f"步骤 {index}"
        log_text = step.get("log") or step.get("message") or step.get("error") or ""
        if is_normal_report and tone == "success":
            log_text = ""
        screenshot_src = _screenshot_src(step.get("screenshot"))

        status_label = _checkpoint_status_label(step)
        baseline_badges = _render_baseline_badges(step)
        baseline_actions = _render_baseline_actions(step)

        log_html = ""
        if log_text:
            highlighted_log = _highlight_vlm_result(log_text)
            log_html = f'<div class="step-details">{highlighted_log}</div>'

        screenshot_html = ""
        if screenshot_src:
            safe_src = _escape_html(screenshot_src)
            screenshot_html = f"""
            <div class="screenshot-container">
                <div class="screenshot-header">
                    <span><i class="fa fa-picture-o"></i> 步骤 {index} 截图</span>
                    <i class="fa fa-chevron-down"></i>
                </div>
                <img class="screenshot-img" src="{safe_src}" alt="步骤 {index} 截图" loading="lazy" decoding="async">
            </div>
            """

        parts.append(
            f"""
            <div class="step-card {tone}">
                <div class="step-header">
                    <div class="step-info">
                        <span class="step-index">{index}</span>
                        <span class="step-name">{_escape_html(action)}{baseline_badges}</span>
                    </div>
                    {baseline_actions}
                    <span class="step-status {tone}">
                        <i class="fa fa-{"check" if tone == "success" else "times" if tone == "danger" else "exclamation"}"></i>
                        {_escape_html(status_label)}
                    </span>
                </div>
                {log_html}
                {screenshot_html}
            </div>
            """
        )
    return "\n".join(parts)


def _get_pass_rate_class(pass_rate: int) -> str:
    if pass_rate >= 80:
        return "success"
    elif pass_rate >= 50:
        return "warning"
    else:
        return "danger"


def _get_status_badge_class(status_tone: str) -> str:
    mapping = {
        "success": "status-success",
        "danger": "status-danger",
        "warning": "status-warning",
        "muted": "status-warning",
    }
    return mapping.get(status_tone, "status-warning")


def _upgrade_relative_artifacts(html: str) -> str:
    """将 HTML 中 ``href="compat/..."`` 形式的相对链接改写为 ``/artifacts/compat/...``。

    落盘的父报告 HTML 使用相对链接（zip 下载到本地后能直接跳转子报告）。
    web 路由读出该 HTML 时，需要把链接还原成 ``/artifacts/...`` 绝对路径，
    这样浏览器从 FastAPI 上下文点击仍能正确解析。仅作用于 ``href`` 属性，
    不修改 ``src``、CSS、JS 等其他位置。
    """
    if not html:
        return html
    # 仅匹配以 compat/ 开头的相对链接，补全 /artifacts/ 前缀（FastAPI 静态挂载点）。
    # 其他相对链接（如 report.html）不误加前缀；绝对链接和特殊协议由负向先行断言排除。
    return re.sub(
        r'href="(?!https?://|/|#|mailto:)(compat/[^"]*)"', r'href="/artifacts/\1"', html
    )


# /artifacts/ 是 FastAPI 静态挂载前缀（见 web_ui/main.py 的 mount("/artifacts", ...)）。
# 下载 ZIP 时必须剥掉该前缀，得到从 compat/ 起的相对路径，本地解压后才能跳转。
_ARTIFACTS_PREFIX = "/artifacts/"


def _localize_artifacts_links(html: str) -> str:
    """将 HTML 中 ``href="/artifacts/compat/..."`` 还原为 ``href="compat/..."``。

    下载场景专用：旧版本落盘的父/子报告 HTML 内可能是 ``/artifacts/...`` 绝对链接，
    打进 ZIP 后本地解压会解析到本机文件系统根目录而失效。剥掉前缀即得到
    ZIP 内的相对路径。对已是 ``compat/...`` 相对链接的 href 保持幂等。
    """
    if not html:
        return html
    pattern = re.compile(r'href="' + re.escape(_ARTIFACTS_PREFIX) + r'(compat/[^"]*)"')
    return pattern.sub(r'href="\1"', html)


def _render_task_report_html(task: Dict[str, Any]) -> str:
    report = _build_report_context(task)
    template_path = os.path.join(TEMPLATES_DIR, "report_viewer.html")
    with open(template_path, "r", encoding="utf-8") as template_file:
        template = template_file.read()

    replacements = {
        "__REPORT_TITLE__": _escape_html(f"{report['title']} - Agent 测试报告"),
        "__REPORT_NAME__": _escape_html(report["title"]),
        "__REPORT_STATUS_CLASS__": _get_status_badge_class(report["status_tone"]),
        "__REPORT_STATUS_LABEL__": _escape_html(report["status_label"]),
        "__REPORT_REMARK__": _escape_html(report["remark"]) if report["remark"] else "",
        "__TEST_TYPE__": _escape_html(report["test_type"]),
        "__CREATED_AT__": _escape_html(report["created_at"]),
        "__COMPLETED_AT__": _escape_html(report["completed_at"]),
        "__REPORT_SUMMARY__": _escape_html(report.get("report_summary", "")),
        "__SUMMARY_CARDS__": _render_summary_cards(report),
        "__PASS_RATE__": str(report["pass_rate"]),
        "__PASS_RATE_CLASS__": _get_pass_rate_class(report["pass_rate"]),
        "__PASS_RATE_STYLE__": f"width: {report['pass_rate']}%;",
        "__STEPS_TITLE__": _escape_html(report["steps_title"]),
        "__STEPS_COUNT_LABEL__": _escape_html(report["steps_count_label"]),
        "__STEPS_HTML__": _render_report_steps(report),
        "__GENERATED_AT__": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    return template


@router.get("/{task_id}/html", response_class=HTMLResponse)
async def api_view_report_html(task_id: int):
    """返回HTML格式报告（多设备报告优先使用已保存的父报告模板）"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.get("task_role") == "parent":
        from backend.compatibility.artifact_store import artifact_store

        rel_path = f"compat/{task_id}/report.html"
        if artifact_store.artifact_exists(rel_path):
            abs_path = artifact_store.get_artifact_path(rel_path)
            with open(abs_path, "r", encoding="utf-8") as f:
                # 落盘 HTML 用相对链接，web 端点开子报告需还原为 /artifacts/...。
                return HTMLResponse(content=_upgrade_relative_artifacts(f.read()))

    return HTMLResponse(content=_render_task_report_html(task))


@router.get("/{task_id}/html/download")
async def api_download_report_html(task_id: int):
    """下载HTML格式报告（多设备报告打包为 ZIP，主+子报告齐备）。"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # 单设备任务：保持原 HTML 下载行为。
    if task.get("task_role") != "parent":
        return HTMLResponse(
            content=_render_task_report_html(task),
            headers={
                "Content-Disposition": f"attachment; filename=report_{task_id}.html"
            },
        )

    # 父报告：把主报告 + 已存在的子报告 HTML 打成 zip 一起返回，
    # 这样本地解压后能完整保留父报告与所有子报告之间的链接关系。
    from backend.compatibility.artifact_store import artifact_store

    parent_rel_path = f"compat/{task_id}/report.html"
    if not artifact_store.artifact_exists(parent_rel_path):
        # 没有已落盘的父报告 HTML（例如异常中断的任务），回退到动态渲染。
        return HTMLResponse(
            content=_render_task_report_html(task),
            headers={
                "Content-Disposition": f"attachment; filename=report_{task_id}.html"
            },
        )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        parent_abs = artifact_store.get_artifact_path(parent_rel_path)
        with open(parent_abs, "r", encoding="utf-8") as f:
            # 剥掉 /artifacts/ 前缀，确保旧报告（绝对链接）解压后也能跳转。
            zf.writestr(f"report_{task_id}.html", _localize_artifacts_links(f.read()))

        for child in get_child_tasks_by_parent(task_id) or []:
            child_id = child.get("id")
            if not child_id:
                continue
            child_rel = f"compat/{task_id}/{child_id}/report_{child_id}.html"
            if not artifact_store.artifact_exists(child_rel):
                continue
            child_abs = artifact_store.get_artifact_path(child_rel)
            with open(child_abs, "r", encoding="utf-8") as f:
                # 保留原 compat/<parent>/<child>/report_<child>.html 相对路径，
                # 同时剥掉 /artifacts/ 前缀，本地解压后链接可定位。
                zf.writestr(child_rel, _localize_artifacts_links(f.read()))

    zip_buffer.seek(0)
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=report_{task_id}.zip"},
    )


@router.post("/batch-export")
async def api_batch_export_reports(data: dict):
    """批量导出报告为ZIP（单ID直接返回HTML）"""
    task_ids = data.get("ids", [])
    if not task_ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")

    if len(task_ids) == 1:
        task_id = int(task_ids[0])
        task = get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return HTMLResponse(
            content=_render_task_report_html(task),
            headers={
                "Content-Disposition": f"attachment; filename=report_{task_id}.html"
            },
        )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for task_id in task_ids:
            task = get_task(task_id)
            if not task:
                continue
            zf.writestr(f"report_{task_id}.html", _render_task_report_html(task))

    zip_buffer.seek(0)

    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=reports_export.zip"},
    )


@router.post("/batch-delete")
async def api_batch_delete_reports(data: dict):
    """批量删除报告（只删除已完成的任务）"""
    task_ids = data.get("ids", [])
    count = batch_delete_reports(task_ids)
    return {"success": True, "deleted_count": count}


@router.delete("/{task_id}/devices/{device_id}")
async def api_delete_report_device(task_id: int, device_id: str):
    """删除报告中单个设备的子任务"""
    success = delete_child_task(task_id, device_id)
    if not success:
        raise HTTPException(status_code=404, detail="未找到该设备的子任务")
    return {"success": True, "message": f"设备 {device_id} 的子任务已删除"}
