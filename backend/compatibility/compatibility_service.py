"""兼容性测试服务 - 提供兼容性测试的核心业务逻辑"""

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.db.database import (
    compute_compat_screenshot_body_hash,
    compute_compat_screenshot_hash,
    compute_compat_screenshot_hash_sha256,
    create_audit_item,
    create_compat_child_task,
    create_compat_parent_task,
    create_compat_vlm_cache,
    find_compat_analysis_baseline,
    find_reusable_compat_analysis_baseline,
    get_assertion_summary,
    get_compat_child_tasks,
    get_compat_parent_task,
    get_device_info,
    get_script,
    get_task_artifacts_by_parent,
    get_task_events_by_parent,
    insert_task_artifact,
    insert_task_event,
    update_compat_analysis_baseline_annotation,
    upsert_compat_analysis_baseline,
    upsert_device,
)
from backend.notification import NotificationEvent, notification_dispatcher
from backend.utils.screenshot_collector import (
    merge_dom_into_captures,
    parse_capture_events,
    parse_dom_signatures,
)
from backend.utils.script_generator import add_screenshot_captures_to_script

from .assertions import AssertionDimension, ErrorCategory
from .device_lock import device_lock_registry
from .event_parser import EventParser, summarize_assertions
from .report_builder import report_builder

logger = logging.getLogger(__name__)

# 兼容性测试的调试日志输出到文件，避免被终端/uvicorn 过滤
_log_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "logs",
)
os.makedirs(_log_dir, exist_ok=True)
logger.setLevel(logging.INFO)
_timeline_handler = logging.FileHandler(
    os.path.join(_log_dir, "compat_timeline.log"), encoding="utf-8", mode="a"
)
_timeline_handler.setFormatter(logging.Formatter("%(asctime)s [COMPAT] %(message)s"))
_timeline_handler.setLevel(logging.INFO)
logger.addHandler(_timeline_handler)
# 关闭向 root logger 传播，避免 [兼容性测试] INFO 日志刷屏 uvicorn 控制台
logger.propagate = False


def _get_worst_severity(result: str):
    """从 VLM result JSON 中提取最严重的 issue severity。

    Returns:
        (header_color: str, description: str) — ("red"/"orange"/"green", 问题描述)
    """
    if not result or result == "{}":
        return "green", ""
    try:
        from .event_parser import safe_json_parse

        result_data = safe_json_parse(result) if isinstance(result, str) else result
        if not isinstance(result_data, dict):
            return "green", ""
        assertions = result_data.get("assertions", []) or result_data.get("results", [])
        worst_severity = None
        worst_description = ""
        sev_order = {"blocker": 5, "major": 4, "minor": 3, "suggestion": 2}
        for assertion in assertions if isinstance(assertions, list) else []:
            if not isinstance(assertion, dict):
                continue
            ev = assertion.get("evidence", {})
            if isinstance(ev, str):
                ev = safe_json_parse(ev)
            if not isinstance(ev, dict):
                continue
            vlm = ev.get("vlm_analysis", {})
            if not isinstance(vlm, dict):
                continue
            issues = vlm.get("issues", [])
            for issue in issues if isinstance(issues, list) else []:
                if not isinstance(issue, dict):
                    continue
                sev = issue.get("severity", "")
                if sev in sev_order and (
                    worst_severity is None or sev_order[sev] > sev_order[worst_severity]
                ):
                    worst_severity = sev
                    worst_description = issue.get("description", "")
    except Exception:
        return "green", ""

    if worst_severity in ("blocker", "major"):
        return "red", worst_description
    elif worst_severity == "minor":
        return "orange", worst_description
    elif worst_severity == "suggestion":
        return "green", worst_description
    return "green", worst_description


def _get_compat_notification_labels(result: str) -> Dict[str, str]:
    """Build four-level compatibility status labels for notifications."""
    severity_label_map = {
        "blocker": "阻塞",
        "major": "严重",
        "minor": "警告",
        "suggestion": "建议",
    }
    severity_order = {"blocker": 4, "major": 3, "minor": 2, "suggestion": 1}
    worst_severity = "suggestion"
    worst_description = ""
    try:
        from .event_parser import safe_json_parse

        result_data = safe_json_parse(result) if isinstance(result, str) else result
        assertions = (
            result_data.get("assertions", []) if isinstance(result_data, dict) else []
        )
        for assertion in assertions if isinstance(assertions, list) else []:
            if not isinstance(assertion, dict):
                continue
            evidence = assertion.get("evidence", {})
            if isinstance(evidence, str):
                evidence = safe_json_parse(evidence)
            if not isinstance(evidence, dict):
                continue
            vlm = evidence.get("vlm_analysis", {})
            if not isinstance(vlm, dict):
                continue
            issues = vlm.get("issues", [])
            for issue in issues if isinstance(issues, list) else []:
                if not isinstance(issue, dict):
                    continue
                severity = str(issue.get("severity", "")).lower()
                if (
                    severity in severity_order
                    and severity_order[severity] > severity_order[worst_severity]
                ):
                    worst_severity = severity
                    worst_description = issue.get("description", "")
    except Exception:
        worst_severity = "suggestion"
        worst_description = ""

    label = severity_label_map[worst_severity]
    summary = f"UI兼容性结果: {label}"
    if worst_description:
        summary += f"；检查结果: {worst_description}"
    return {
        "status": label,
        "severity": worst_severity,
        "summary": summary,
        "warning_description": worst_description,
    }


def _analysis_has_issues(analysis_result: Dict[str, Any]) -> bool:
    issues = list(analysis_result.get("issues") or [])
    for dim_result in analysis_result.get("dimensions", []) or []:
        issues.extend(dim_result.get("issues", []) or [])
    return bool(issues)


def _resolve_device_display_name(device_id: str) -> str:
    """解析设备展示名：优先实时 mcp_tools 查询，失败回退 DB 库存，最终回退「未知设备」。

    避免在飞书通知、报告卡片中暴露设备序列号。
    """
    if not device_id:
        return "未知设备"
    # 1. 实时查询（设备在线时可拿到 live model/version）
    try:
        from backend.mcp.mcp_tools import mcp_tools

        dev = mcp_tools.get_device_info(device_id)
        if dev and getattr(dev, "model", ""):
            version = getattr(dev, "version", "") or ""
            brand = getattr(dev, "brand", "") or ""
            parts = [p for p in (brand, dev.model) if p]
            if parts:
                name = " ".join(parts)
                if version:
                    name += f" (android {version})"
                return name
    except Exception:
        pass
    # 2. DB 库存回退
    try:
        from backend.db.database import get_device_display_name_from_db

        db_name = get_device_display_name_from_db(device_id)
        if db_name:
            return db_name
    except Exception:
        pass
    # 3. 最终回退：不再展示序列号
    return "未知设备"


class CompatibilityService:
    """兼容性测试服务"""

    def __init__(self):
        self._running_tasks = set()
        self._websocket_callbacks = []
        self._vlm_semaphore = asyncio.Semaphore(
            int(os.getenv("COMPAT_VLM_CONCURRENCY", "2"))
        )

    def register_websocket_callback(self, callback):
        """注册WebSocket回调"""
        self._websocket_callbacks.append(callback)

    def unregister_websocket_callback(self, callback):
        """取消注册WebSocket回调"""
        if callback in self._websocket_callbacks:
            self._websocket_callbacks.remove(callback)

    async def _broadcast(self, event):
        """广播事件到所有WebSocket客户端"""
        for callback in self._websocket_callbacks[:]:
            try:
                await callback(event)
            except Exception:
                pass

    def _notification_status_from_severity(self, severity: str) -> tuple[str, str]:
        severity = (severity or "").lower()
        if severity in {"blocker", "critical"}:
            return "严重", "blocker"
        if severity in {"major", "severe"}:
            return "严重", "major"
        if severity in {"minor", "warning", "warn"}:
            return "警告", "minor"
        if severity in {"suggestion", "info"}:
            return "建议", "suggestion"
        return "", ""

    def _get_notification_status(
        self, device_result: Dict[str, Any]
    ) -> tuple[str, str]:
        status, severity = self._notification_status_from_severity(
            device_result.get("key_issue_severity", "")
        )
        if severity:
            return status, severity

        summary = device_result.get("assertion_summary", {}) or {}
        if device_result.get("status") == "failed" or summary.get("failed", 0) > 0:
            return "严重", "major"
        if (
            device_result.get("status") == "partial_failed"
            or summary.get("warning", 0) > 0
            or summary.get("pending_review", 0) > 0
        ):
            return "警告", "minor"
        return "完成", ""

    async def _notify_device_completed(
        self, device_result: Dict[str, Any], report_url: Optional[str]
    ) -> None:
        status, severity = self._get_notification_status(device_result)
        summary = device_result.get("assertion_summary", {}) or {}
        total = summary.get("total", 0)
        failed = summary.get("failed", 0)
        if total and failed:
            result_text = f"发现 {failed} 项失败 / 共 {total} 项检查"
        elif total:
            result_text = f"{total} 项检查全部通过"
        else:
            result_text = "兼容性测试完成（无断言）"

        completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await notification_dispatcher.notify(
            NotificationEvent(
                event_type="compat_device_completed",
                task_name=(
                    f"兼容性测试- "
                    f"{device_result.get('device_label') or device_result.get('device_id')}"
                ),
                status=status,
                severity=severity,
                completed_at=completed_at,
                result=result_text,
                device_id=(
                    device_result.get("device_label")
                    or device_result.get("device_id")
                    or ""
                ),
                task_type="compatibility",
                role="child",
                extra={
                    "warning_description": device_result.get("key_issue", ""),
                    "report_url": report_url or "",
                },
            )
        )

    async def create_compat_task(
        self,
        script_id: int,
        device_ids: List[str],
        platform: str = "Android",
        remark: str = "",
        project_id: int = None,
        compatibility_dimensions: List[str] = None,
        script_ids: List[int] = None,
    ) -> Dict[str, Any]:
        """
        创建兼容性测试任务

        Args:
            script_id: 脚本ID
            device_ids: 设备ID列表
            platform: 平台类型
            remark: 备注
            project_id: 项目ID
            compatibility_dimensions: 兼容性校验维度列表

        Returns:
            任务信息
        """
        # 记录创建日志
        compat_script_ids = script_ids or [script_id]
        logger.info(
            "[兼容性测试] 创建任务: script_id=%s, script_count=%s, devices=%s, dimensions=%s",
            script_id,
            len(compat_script_ids),
            device_ids,
            compatibility_dimensions,
        )

        # 创建父任务
        parent_task_id = create_compat_parent_task(
            script_id=script_id,
            device_ids=device_ids,
            remark=remark,
            project_id=project_id,
            platform=platform,
            compatibility_dimensions=compatibility_dimensions,
            compat_script_ids=compat_script_ids,
        )

        # 创建子任务
        child_task_ids = []
        for device_id in device_ids:
            child_task_id = create_compat_child_task(
                parent_task_id=parent_task_id,
                script_id=script_id,
                device_id=device_id,
                platform=platform,
            )
            child_task_ids.append(
                {
                    "child_task_id": child_task_id,
                    "device_id": device_id,
                }
            )

        return {
            "parent_task_id": parent_task_id,
            "child_tasks": child_task_ids,
            "device_count": len(device_ids),
            "platform": platform,
        }

    async def execute_compat_task(
        self,
        parent_task_id: int,
        device_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        from backend.db.database import update_task

        try:
            if parent_task_id in self._running_tasks:
                raise RuntimeError("任务已在运行中")

            parent_task = get_compat_parent_task(parent_task_id)
            if not parent_task:
                raise ValueError(f"父任务不存在: {parent_task_id}")

            compat_scripts = self._get_parent_compat_scripts(parent_task)
            if not compat_scripts:
                raise ValueError("脚本内容为空")

            device_id_str = parent_task.get("device_id") or ""
            target_device_ids = device_ids or (
                device_id_str.split(",") if device_id_str else []
            )
            if not target_device_ids:
                raise ValueError("没有指定测试设备")

            trace_id = str(uuid.uuid4())[:8]
            if not device_lock_registry.batch_acquire(
                target_device_ids, str(parent_task_id)
            ):
                raise RuntimeError("无法获取设备锁，部分设备可能被占用")

            update_task(parent_task_id, status="running")
            self._running_tasks.add(parent_task_id)

            await self._broadcast(
                {
                    "type": "compat_event",
                    "event": "parent_status",
                    "parent_task_id": parent_task_id,
                    "status": "running",
                }
            )

            async def execute_device_scripts(device_id: str) -> List[asyncio.Task]:
                child_task = self._get_or_create_child_task(parent_task_id, device_id)
                child_task_id = child_task["id"]
                update_task(child_task_id, status="running")
                phase2_tasks = []
                platform = parent_task.get("platform", "Android")

                try:
                    for script in compat_scripts:
                        script_index = int(script.get("script_index") or 0)
                        script_trace_id = f"{trace_id}-{device_id}-s{script_index}"
                        try:
                            p1_result = await self._exec_child_phase1(
                                parent_task_id=parent_task_id,
                                child_task_id=child_task_id,
                                script_content=script["content"],
                                device_id=device_id,
                                platform=platform,
                                trace_id=script_trace_id,
                            )
                        except Exception as script_error:
                            error_msg = str(script_error)
                            logger.error(
                                "[兼容性测试] 脚本执行异常但继续后续脚本: child_task_id=%s, script_id=%s, error=%s",
                                child_task_id,
                                script.get("id"),
                                error_msg,
                                exc_info=True,
                            )
                            p1_result = {
                                "child_task_id": child_task_id,
                                "device_id": device_id,
                                "parent_task_id": parent_task_id,
                                "platform": platform,
                                "trace_id": script_trace_id,
                                "script_success": False,
                                "output": error_msg,
                                "raw_output": "",
                                "error": error_msg,
                                "assertion_summary": {
                                    "total": 0,
                                    "passed": 0,
                                    "failed": 0,
                                    "warning": 0,
                                },
                                "capture_events": [],
                                "vlm_events": [],
                            }

                        p1_result["script_id"] = script.get("id")
                        p1_result["script_name"] = script.get("name")
                        p1_result["script_index"] = script_index
                        for capture_event in p1_result.get("capture_events", []):
                            capture_event.setdefault("script_id", script.get("id"))
                            capture_event.setdefault("script_name", script.get("name"))
                            capture_event.setdefault("script_index", script_index)

                        logger.info(
                            "[兼容性测试] Phase1 完成, 已调度 Phase2: device=%s, script_index=%s, captures=%s",
                            device_id,
                            script_index,
                            len(p1_result.get("capture_events", [])),
                        )

                        phase2_tasks.append(
                            asyncio.create_task(
                                self._exec_child_phase2(p1_result, parent_task_id)
                            )
                        )
                        logger.info(
                            "[兼容性测试] Phase2 任务已创建(事件循环中排队等待执行): device=%s, script_index=%s",
                            device_id,
                            script_index,
                        )
                        await asyncio.sleep(0)
                finally:
                    device_lock_registry.release(device_id, str(parent_task_id))
                    logger.info(f"[兼容性测试] 设备锁已释放: device={device_id}")

                return phase2_tasks

            # ========== Phase 1: 执行脚本（持有设备锁）；每个脚本完成后立即提交 Phase 2 ==========
            device_tasks = [
                asyncio.create_task(execute_device_scripts(device_id))
                for device_id in target_device_ids
            ]

            # ========== Phase 2: VLM 分析 + 状态更新（无锁） ==========
            phase2_tasks = []
            for coro in asyncio.as_completed(device_tasks):
                phase2_tasks.extend(await coro)

            script_results = await asyncio.gather(*phase2_tasks) if phase2_tasks else []
            device_results = self._group_results_by_device(script_results)

            # 汇总结果
            summary = await self._aggregate_results(parent_task_id, device_results)

            all_finished = all(r["status"] == "finished" for r in device_results)
            has_failed = any(r["status"] == "failed" for r in device_results)
            has_partial = any(r["status"] == "partial_failed" for r in device_results)

            if all_finished:
                status = "finished"
            elif has_failed:
                status = "failed"
            elif has_partial:
                status = "partial_failed"
            else:
                status = "partial_failed"

            for device_result in device_results:
                child_task_id = device_result.get("child_task_id")
                if child_task_id:
                    update_task(
                        child_task_id,
                        status=device_result.get("status", "partial_failed"),
                        result=json.dumps(device_result, ensure_ascii=False),
                        completed_at=datetime.now(timezone.utc).isoformat(),
                    )

            update_task(
                parent_task_id,
                status=status,
                result=json.dumps(summary),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

            try:
                child_report_paths = (
                    await self._generate_report(parent_task_id, summary)
                ) or {}
            except Exception as e:
                import logging

                logging.error(
                    f"Failed to generate report for task {parent_task_id}: {e}"
                )
                child_report_paths = {}

            for device_result in device_results:
                child_task_id = device_result.get("child_task_id")
                child_report_path = (
                    child_report_paths.get(child_task_id) if child_task_id else None
                )
                report_url = (
                    f"/artifacts/{child_report_path}" if child_report_path else ""
                )
                try:
                    await self._notify_device_completed(device_result, report_url)
                except Exception as notify_err:
                    logger.error(
                        "[兼容性测试] 发送设备通知失败: %s",
                        notify_err,
                        exc_info=True,
                    )

            # 发送 batch 级别飞书通知（一个 batch 只发一次，不再每个脚本发一条）
            try:
                total_assertions = summary.get("assertion_total", 0)
                total_failed = summary.get("assertion_failed", 0)

                if total_assertions > 0 and total_failed > 0:
                    batch_status = "严重"
                    batch_severity = "major"
                    batch_summary = f"兼容性测试完成: {total_assertions}项断言, {total_failed}项失败"
                elif total_assertions > 0:
                    batch_status = "完成"
                    batch_severity = ""
                    batch_summary = f"兼容性测试完成: {total_assertions}项断言全部通过"
                else:
                    batch_status = "完成"
                    batch_severity = ""
                    batch_summary = "兼容性测试完成（无断言）"

                # 解析设备型号（实时查询失败时回退到 DB 库存，避免展示序列号）
                device_names = [
                    _resolve_device_display_name(did) for did in target_device_ids
                ]
                device_info_str = " / ".join(device_names)

                # 提取最高严重级别的 VLM 问题描述
                severity_order = {"blocker": 0, "major": 1, "minor": 2, "suggestion": 3}
                highest_issue = None
                all_events = get_task_events_by_parent(parent_task_id)
                for event in all_events:
                    if event.get("event_type") != "assertion":
                        continue
                    evidence = event.get("evidence")
                    if not evidence:
                        continue
                    try:
                        ev = (
                            json.loads(evidence)
                            if isinstance(evidence, str)
                            else evidence
                        )
                    except (json.JSONDecodeError, TypeError):
                        continue
                    vlm = ev.get("vlm_analysis")
                    if not vlm:
                        continue
                    issues = vlm.get("issues", [])
                    for issue in issues:
                        sev = issue.get("severity", "suggestion")
                        rank = severity_order.get(sev, 99)
                        if highest_issue is None or rank < severity_order.get(
                            highest_issue["severity"], 99
                        ):
                            highest_issue = {
                                "severity": sev,
                                "category": issue.get("category", ""),
                                "description": issue.get("description", ""),
                            }

                if highest_issue:
                    warning_description = f"[{highest_issue['severity']}] {highest_issue['category']}: {highest_issue['description']}"
                    severity_status, severity_value = (
                        self._notification_status_from_severity(
                            highest_issue["severity"]
                        )
                    )
                    if severity_value:
                        batch_status = severity_status
                        batch_severity = severity_value
                else:
                    warning_description = ""

                finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                asyncio.create_task(
                    notification_dispatcher.notify(
                        NotificationEvent(
                            event_type="compat_completed",
                            task_name="兼容性测试汇总",
                            status=batch_status,
                            severity=batch_severity,
                            completed_at=finished_at,
                            result=batch_summary,
                            device_id=device_info_str,
                            task_type="compatibility",
                            role="parent",
                            extra={
                                "device_list": device_info_str,
                                "report_url": f"/api/reports/{parent_task_id}/html",
                                "device_count": len(target_device_ids),
                                "script_count": len(compat_scripts),
                                "warning_description": warning_description,
                            },
                        )
                    )
                )
            except Exception as notify_err:
                logger.error(f"[兼容性测试] 发送汇总通知失败: {notify_err}")

            await self._broadcast(
                {
                    "type": "compat_event",
                    "event": "summary_updated",
                    "parent_task_id": parent_task_id,
                    "platform": parent_task.get("platform"),
                    "status": status,
                    **summary,
                }
            )

            return summary

        except Exception as e:
            import logging

            logging.error(
                f"兼容性测试执行失败 parent_task_id={parent_task_id}: {e}",
                exc_info=True,
            )
            update_task(
                parent_task_id,
                status="failed",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            raise

        finally:
            device_lock_registry.release_all(str(parent_task_id))
            self._running_tasks.discard(parent_task_id)

    def _get_or_create_child_task(self, parent_task_id: int, device_id: str):
        """获取或创建子任务"""
        from backend.db.database import (
            create_compat_child_task,
            get_compat_task_by_device,
        )

        child_task = get_compat_task_by_device(parent_task_id, device_id)
        if child_task:
            return child_task

        # 创建新的子任务
        parent_task = get_compat_parent_task(parent_task_id)
        if parent_task:
            child_task_id = create_compat_child_task(
                parent_task_id=parent_task_id,
                script_id=parent_task["script_id"],
                device_id=device_id,
                platform=parent_task.get("platform", "Android"),
            )
            return {"id": child_task_id, "device_id": device_id}

        raise ValueError(f"无法创建子任务 {parent_task_id}, {device_id}")

    def _get_parent_compat_scripts(
        self, parent_task: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """解析兼容性父任务需要执行的脚本列表。"""
        extra = {}
        try:
            extra = json.loads(parent_task.get("extra") or "{}")
        except (TypeError, json.JSONDecodeError):
            extra = {}

        script_ids = extra.get("compat_script_ids") or [parent_task.get("script_id")]
        scripts = []
        for index, script_id in enumerate(script_ids):
            if not script_id:
                continue
            script = get_script(int(script_id))
            if script:
                scripts.append(
                    {
                        "id": script["id"],
                        "name": script.get("name") or f"script-{script_id}",
                        "content": script.get("content") or "",
                        "script_index": index,
                    }
                )
                continue
            if index == 0 and parent_task.get("script_content"):
                scripts.append(
                    {
                        "id": int(script_id),
                        "name": parent_task.get("name") or f"script-{script_id}",
                        "content": parent_task["script_content"],
                        "script_index": index,
                    }
                )
            else:
                logger.warning(
                    "[兼容性测试] 脚本不存在，已跳过: script_id=%s",
                    script_id,
                )
        return scripts

    def _group_capture_events_by_script(
        self, capture_events: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        groups: Dict[int, List[Dict[str, Any]]] = {}
        for event in capture_events:
            script_index = int(event.get("script_index") or 0)
            groups.setdefault(script_index, []).append(event)
        return [
            {"script_index": script_index, "capture_events": events}
            for script_index, events in sorted(groups.items())
        ]

    async def _exec_child_phase1(
        self,
        parent_task_id: int,
        child_task_id: int,
        script_content: str,
        device_id: str,
        platform: str,
        trace_id: str,
    ) -> Dict[str, Any]:
        """Phase 1: 执行脚本 + 解析事件（持有设备锁）。返回中间数据供 Phase 2 消费。"""
        from backend.db.database import update_task

        logger.info(
            f"[兼容性测试] Phase1 开始 child_task_id={child_task_id}, device_id={device_id}"
        )
        logger.debug(f"[兼容性测试] 脚本长度: {len(script_content)} 字符")

        try:
            await self._broadcast(
                {
                    "type": "compat_event",
                    "event": "child_status",
                    "parent_task_id": parent_task_id,
                    "child_task_id": child_task_id,
                    "device_id": device_id,
                    "platform": platform,
                    "status": "running",
                }
            )

            import os
            import tempfile

            current_env = os.environ.copy()

            vlm_event_fd, vlm_event_path = tempfile.mkstemp(
                suffix=".vlm_events", prefix="vlm_", text=True
            )
            os.close(vlm_event_fd)
            current_env.update(
                {
                    "COMPAT_PARENT_TASK_ID": str(parent_task_id),
                    "COMPAT_CHILD_TASK_ID": str(child_task_id),
                    "COMPAT_TRACE_ID": trace_id,
                    "COMPAT_PLATFORM": platform,
                    "DEVICE_ID": device_id,
                    "VLM_EVENT_FILE": vlm_event_path,
                }
            )

            device_type = (
                "adb"
                if platform.lower() == "android"
                else ("hdc" if platform.lower() == "harmonyos" else "ios")
            )
            script_content_with_captures = add_screenshot_captures_to_script(
                script_content, device_type
            )
            if script_content_with_captures != script_content:
                logger.info(
                    f"[兼容性测试] 已注入截图捕获: child_task_id={child_task_id}"
                )

            vlm_events = []
            try:
                logger.info(f"[兼容性测试] 开始执行脚本 child_task_id={child_task_id}")
                result = await asyncio.to_thread(
                    self._execute_script_sync,
                    script_content_with_captures,
                    device_id,
                    current_env,
                )

                logger.info(
                    f"[兼容性测试] 脚本执行完成: child_task_id={child_task_id}, success={result['success']}, returncode={result.get('returncode', 'N/A')}"
                )

                stdout_snippet = (result["output"] or "")[:3000]
                if stdout_snippet:
                    logger.info(
                        f"[兼容性测试] 脚本 stdout 片段: child_task_id={child_task_id}, output={repr(stdout_snippet)}"
                    )
                else:
                    logger.warning(
                        f"[兼容性测试] 脚本 stdout 为空: child_task_id={child_task_id}"
                    )

                full_output = result["output"] or ""
                has_debug = "[CAPTURE_DEBUG]" in full_output
                has_capture = "[CAPTURE_EVENT]" in full_output
                has_pytest_pass = "PASSED" in full_output or "passed" in full_output
                has_pytest_fail = "FAILED" in full_output or "failed" in full_output
                has_error = "Error" in full_output or "ERROR" in full_output
                logger.info(
                    f"[兼容性测试] 脚本输出诊断: child_task_id={child_task_id}, debug={has_debug}, capture={has_capture}, test_pass={has_pytest_pass}, test_fail={has_pytest_fail}, error={has_error}, total_len={len(full_output)}"
                )

                if result.get("error"):
                    logger.warning(
                        f"[兼容性测试] 脚本错误: child_task_id={child_task_id}, error={result['error'][:500]}"
                    )

                if os.path.exists(vlm_event_path):
                    with open(vlm_event_path, "r", encoding="utf-8") as f:
                        vlm_file_content = f.read()
                    vlm_events = EventParser.parse_output(vlm_file_content)
                    logger.info(
                        f"[兼容性测试] VLM事件文件解析: child_task_id={child_task_id}, event_count={len(vlm_events)}"
                    )

            finally:
                if os.path.exists(vlm_event_path):
                    try:
                        os.unlink(vlm_event_path)
                    except OSError:
                        pass

            stdout_events = EventParser.parse_output(result["output"])

            seen = set()
            events = []
            for ev in vlm_events + stdout_events:
                key = (ev.get("type"), ev.get("name"), ev.get("step_index"))
                if key not in seen:
                    seen.add(key)
                    events.append(ev)

            logger.info(
                f"[兼容性测试] 合并后事件: child_task_id={child_task_id}, event_count={len(events)} (文件={len(vlm_events)}, stdout={len(stdout_events)})"
            )

            for event_data in events:
                await self._handle_event(
                    event_data,
                    child_task_id,
                    parent_task_id,
                    device_id,
                    platform,
                    trace_id,
                )

            assertion_summary = summarize_assertions(events).to_dict()
            logger.info(
                f"[兼容性测试] 断言汇总 child_task_id={child_task_id}, passed={assertion_summary['passed']}, failed={assertion_summary['failed']}"
            )

            vlm_json_lines = (
                "\n".join(json.dumps(ev, ensure_ascii=False) for ev in vlm_events)
                if vlm_events
                else ""
            )
            combined_output = result["output"]
            if vlm_json_lines:
                combined_output = (
                    combined_output.rstrip("\n") + "\n" + vlm_json_lines + "\n"
                )

            capture_events = parse_capture_events(result["output"])
            logger.info(
                f"[兼容性测试] 捕获事件解析: child_task_id={child_task_id}, capture_count={len(capture_events)}"
            )

            dom_signatures = parse_dom_signatures(result["output"])
            if dom_signatures:
                capture_events = merge_dom_into_captures(capture_events, dom_signatures)
                logger.info(
                    f"[兼容性测试] DOM签名合并: child_task_id={child_task_id}, dom_count={len(dom_signatures)}, merged_captures={len(capture_events)}"
                )

            return {
                "child_task_id": child_task_id,
                "device_id": device_id,
                "parent_task_id": parent_task_id,
                "platform": platform,
                "trace_id": trace_id,
                "script_success": result["success"],
                "output": combined_output,
                "raw_output": result["output"],
                "error": result.get("error", ""),
                "assertion_summary": assertion_summary,
                "capture_events": capture_events,
                "vlm_events": vlm_events,
            }

        except Exception as e:
            error_msg = str(e)
            logger.error(
                f"[兼容性测试] Phase1 异常 child_task_id={child_task_id}: {error_msg}",
                exc_info=True,
            )
            update_task(
                child_task_id,
                status="failed",
                result=json.dumps({"success": False, "error": error_msg}),
                log=error_msg,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            await self._broadcast(
                {
                    "type": "compat_event",
                    "event": "error",
                    "parent_task_id": parent_task_id,
                    "child_task_id": child_task_id,
                    "device_id": device_id,
                    "platform": platform,
                    "category": ErrorCategory.SCRIPT_ERROR.value,
                    "message": error_msg,
                }
            )
            return {
                "child_task_id": child_task_id,
                "device_id": device_id,
                "parent_task_id": parent_task_id,
                "platform": platform,
                "trace_id": trace_id,
                "script_success": False,
                "output": error_msg,
                "raw_output": "",
                "error": error_msg,
                "assertion_summary": {
                    "total": 0,
                    "passed": 0,
                    "failed": 0,
                    "warning": 0,
                },
                "capture_events": [],
                "vlm_events": [],
            }

    async def _exec_child_phase2(
        self,
        p1_result: Dict[str, Any],
        parent_task_id: int,
    ) -> Dict[str, Any]:
        """Phase 2: VLM 分析 + 状态更新 + 通知（无设备锁）。"""
        from backend.db.database import update_task

        child_task_id = p1_result["child_task_id"]
        device_id = p1_result["device_id"]
        platform = p1_result["platform"]
        capture_events = p1_result["capture_events"]
        script_index = p1_result.get("script_index", "?")

        logger.info(
            "[兼容性测试] Phase2 开始执行(script_index=%s): device=%s, captures=%s",
            script_index,
            device_id,
            len(capture_events),
        )

        if not p1_result["script_success"] and not capture_events:
            logger.info(
                f"[兼容性测试] Phase2 跳过 VLM（脚本失败且无截图）: child_task_id={child_task_id}"
            )
            return {
                "child_task_id": child_task_id,
                "device_id": device_id,
                "status": "failed",
                "assertion_summary": p1_result["assertion_summary"],
                "success": False,
                "output": p1_result["output"],
                "error": p1_result["error"],
                "script_id": p1_result.get("script_id"),
                "script_name": p1_result.get("script_name"),
                "script_index": p1_result.get("script_index"),
            }

        assertion_summary = p1_result["assertion_summary"]
        combined_output = p1_result["output"]

        vlm_analysis_result = None
        if capture_events:
            logger.info(
                f"[兼容性测试] 触发批量 VLM 分析: child_task_id={child_task_id}, captures={len(capture_events)}"
            )
            try:
                assertion_json_lines = []
                grouped_results = []
                for group in self._group_capture_events_by_script(capture_events):
                    group_result = await self._batch_analyze_captures(
                        parent_task_id=parent_task_id,
                        child_task_id=child_task_id,
                        device_id=device_id,
                        platform=platform,
                        capture_events=group["capture_events"],
                    )
                    if group_result:
                        grouped_results.append(group_result)
                        assertion_json_lines.extend(
                            group_result.get("assertion_json_lines", [])
                        )

                if grouped_results:
                    assertion_summary = {
                        "total": 0,
                        "passed": 0,
                        "failed": 0,
                        "warning": 0,
                        "skipped": 0,
                        "by_dimension": {
                            dim.value: {
                                "total": 0,
                                "passed": 0,
                                "failed": 0,
                                "warning": 0,
                            }
                            for dim in AssertionDimension
                        },
                    }
                    for group_result in grouped_results:
                        group_summary = group_result.get("assertion_summary", {})
                        for key in ("total", "passed", "failed", "warning", "skipped"):
                            assertion_summary[key] += int(
                                group_summary.get(key, 0) or 0
                            )
                        for dim_name, dim_counts in (
                            group_summary.get("by_dimension", {}) or {}
                        ).items():
                            if dim_name not in assertion_summary["by_dimension"]:
                                continue
                            for key in ("total", "passed", "failed", "warning"):
                                assertion_summary["by_dimension"][dim_name][key] += int(
                                    dim_counts.get(key, 0) or 0
                                )
                    vlm_analysis_result = {
                        "assertion_summary": assertion_summary,
                        "assertion_json_lines": assertion_json_lines,
                    }
            except Exception as e:
                logger.error(
                    f"[兼容性测试] 批量 VLM 分析失败: child_task_id={child_task_id}, error={str(e)}"
                )

        has_vlm_failure = assertion_summary["failed"] > 0

        if vlm_analysis_result:
            assertion_json_lines = vlm_analysis_result.get("assertion_json_lines", [])
            if assertion_json_lines:
                for ev in assertion_json_lines:
                    combined_output += "\n" + json.dumps(ev, ensure_ascii=False) + "\n"
                logger.debug(
                    f"[兼容性测试] 断言事件已写入 output: child_task_id={child_task_id}, count={len(assertion_json_lines)}"
                )

        for ev in capture_events:
            ev.pop("screenshot_base64", None)

        status = (
            "finished"
            if p1_result["script_success"] and not has_vlm_failure
            else "failed"
        )

        task_error = p1_result.get("error") or ""
        if not task_error and not p1_result["script_success"]:
            task_error = "Script execution failed (see log for details)"

        update_task(
            child_task_id,
            status=status,
            result=json.dumps(
                {
                    "success": p1_result["script_success"],
                    "error": task_error,
                    "assertion_summary": assertion_summary,
                }
            ),
            log=combined_output,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        await self._broadcast(
            {
                "type": "compat_event",
                "event": "child_status",
                "parent_task_id": parent_task_id,
                "child_task_id": child_task_id,
                "device_id": device_id,
                "platform": platform,
                "status": status,
                "assertion_summary": assertion_summary,
            }
        )

        return {
            "child_task_id": child_task_id,
            "device_id": device_id,
            "status": status,
            "assertion_summary": assertion_summary,
            "success": p1_result["script_success"],
            "output": combined_output,
            "error": p1_result.get("error", ""),
            "script_id": p1_result.get("script_id"),
            "script_name": p1_result.get("script_name"),
            "script_index": p1_result.get("script_index"),
        }

    async def _execute_child_task(
        self,
        parent_task_id: int,
        child_task_id: int,
        script_content: str,
        device_id: str,
        platform: str,
        trace_id: str,
    ):
        """执行单个子任务（两阶段包装，保持向后兼容）。"""
        p1 = await self._exec_child_phase1(
            parent_task_id=parent_task_id,
            child_task_id=child_task_id,
            script_content=script_content,
            device_id=device_id,
            platform=platform,
            trace_id=trace_id,
        )
        p2 = await self._exec_child_phase2(p1, parent_task_id)
        return p2

    async def _batch_analyze_captures(
        self,
        parent_task_id: int,
        child_task_id: int,
        device_id: str,
        platform: str,
        capture_events: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """批量分析捕获的截图（延迟 VLM 分析）

        Args:
            parent_task_id: 父任务 ID
            child_task_id: 子任务 ID
            device_id: 设备 ID
            platform: 平台类型
            capture_events: 捕获事件列表

        Returns:
            分析结果，包含断言汇总
        """
        from backend.compatibility.vlm_ui_analyzer import VLMUIAnalyzer
        from backend.compatibility.vlm_ui_markup import annotate_base64

        logger.info(
            f"[兼容性测试] 开始批量 VLM 分析: child_task_id={child_task_id}, captures={len(capture_events)}"
        )

        try:
            # 获取父任务配置的维度列表
            parent_task = get_compat_parent_task(parent_task_id)
            compatibility_dimensions = []
            project_id = 0
            if parent_task:
                project_id = int(parent_task.get("project_id") or 0)
                extra_info = parent_task.get("extra", "{}")
                try:
                    from .event_parser import safe_json_parse

                    extra_dict = safe_json_parse(extra_info)
                    if extra_dict:
                        compatibility_dimensions = extra_dict.get(
                            "compatibility_dimensions", []
                        )
                except Exception:
                    pass
            logger.info(
                f"[兼容性测试] 使用维度配置: parent_task_id={parent_task_id}, dimensions={compatibility_dimensions}"
            )

            from backend.compatibility.vlm_ui_analyzer import VLMUIAnalyzer

            if not compatibility_dimensions:
                compatibility_dimensions = VLMUIAnalyzer.DEFAULT_DIMENSIONS
                logger.info(
                    f"[兼容性测试] 用户未选择维度，使用完整6维度: {compatibility_dimensions}"
                )

            # 汇总断言
            assertion_summary = {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "warning": 0,
                "skipped": 0,
                "by_dimension": {
                    dim.value: {"total": 0, "passed": 0, "failed": 0, "warning": 0}
                    for dim in AssertionDimension
                },
            }
            assertion_json_lines = []

            # 批量分析每个捕获（使用 asyncio.to_thread 避免阻塞事件循环）
            for idx, capture_event in enumerate(capture_events):
                step_name = capture_event.get("step_name", f"Step {idx + 1}")
                image_size = capture_event.get("image_size", 0)
                script_id = capture_event.get("script_id")
                script_name = capture_event.get("script_name")
                script_index = capture_event.get("script_index")

                logger.debug(
                    f"[兼容性测试] 分析捕获 {idx + 1}/{len(capture_events)}: step_name={step_name}, size={image_size}"
                )

                try:
                    # 使用脚本执行时捕获的截图数据，而不是重新截图
                    screenshot_base64 = capture_event.get("screenshot_base64")

                    if screenshot_base64:
                        dom_hash = capture_event.get("dom_hash", "")
                        activity = capture_event.get("activity", "")
                        screenshot_hash = compute_compat_screenshot_hash(
                            screenshot_base64
                        )
                        screenshot_hash_sha256 = compute_compat_screenshot_hash_sha256(
                            screenshot_base64
                        )
                        screenshot_body_hash = compute_compat_screenshot_body_hash(
                            screenshot_base64
                        )
                        cache_hit = False
                        analysis_state = "new"
                        baseline_record = None
                        fixed_baseline = None
                        existing_baseline = find_compat_analysis_baseline(
                            project_id=project_id,
                            device_id=device_id,
                            activity=activity,
                            step_name=step_name,
                            dom_hash=dom_hash,
                            screenshot_hash=screenshot_hash,
                            screenshot_body_hash=screenshot_body_hash,
                        )
                        if (
                            existing_baseline
                            and existing_baseline.get("review_status") == "fixed"
                        ):
                            fixed_baseline = existing_baseline

                        reviewed_baseline = find_reusable_compat_analysis_baseline(
                            project_id=project_id,
                            device_id=device_id,
                            activity=activity,
                            step_name=step_name,
                            dom_hash=dom_hash,
                            screenshot_hash=screenshot_hash,
                            screenshot_body_hash=screenshot_body_hash,
                        )
                        if reviewed_baseline:
                            analysis_result = json.loads(
                                reviewed_baseline["vlm_result"]
                            )
                            analysis_state = "reused"
                            baseline_record = reviewed_baseline
                            cache_hit = True
                            logger.info(
                                "[兼容性测试] 复用已审核UI基线: baseline_id=%s, status=%s, step_name=%s",
                                reviewed_baseline["id"],
                                reviewed_baseline["review_status"],
                                step_name,
                            )

                        if not cache_hit and screenshot_hash:
                            from backend.db.database import execute_query as _eq

                            for candidate_hash in (
                                screenshot_hash,
                                screenshot_hash_sha256,
                            ):
                                broad_rows = _eq(
                                    "SELECT * FROM compat_analysis_baselines "
                                    "WHERE screenshot_hash = ? AND review_status != 'fixed' "
                                    "ORDER BY updated_at DESC LIMIT 1",
                                    (candidate_hash,),
                                )
                                if broad_rows:
                                    broad_baseline = broad_rows[0]
                                    analysis_result = json.loads(
                                        broad_baseline["vlm_result"]
                                    )
                                    analysis_state = "reused"
                                    baseline_record = broad_baseline
                                    cache_hit = True
                                    logger.debug(
                                        "[兼容性测试] 宽泛匹配复用UI基线: baseline_id=%s, status=%s, step_name=%s (原hash=%s)",
                                        broad_baseline["id"],
                                        broad_baseline["review_status"],
                                        broad_baseline.get("step_name", ""),
                                        candidate_hash[:12],
                                    )
                                    break

                        if not cache_hit:
                            from backend.db.database import execute_query as _eq_diag

                            _existing = _eq_diag(
                                "SELECT id, step_name, screenshot_hash, review_status FROM compat_analysis_baselines "
                                "WHERE step_name = ? AND (screenshot_hash = ? OR screenshot_hash = ?) "
                                "ORDER BY updated_at DESC LIMIT 3",
                                (step_name, screenshot_hash, screenshot_hash_sha256),
                            )
                            logger.debug(
                                "[兼容性测试] VLM未命中缓存: step=%s, pHash=%s, SHA256=%s, "
                                "匹配候选=%d条, exist_rows=%s",
                                step_name,
                                screenshot_hash[:16],
                                screenshot_hash_sha256[:16],
                                len(_existing),
                                [
                                    {
                                        "id": r["id"],
                                        "hash": r["screenshot_hash"][:12],
                                        "status": r["review_status"],
                                    }
                                    for r in _existing
                                ]
                                if _existing
                                else "none",
                            )
                            # 在线程池中执行 VLM 分析，避免阻塞事件循环；外层限制并发。
                            analysis_result = await self._analyze_capture_with_limit(
                                screenshot_base64,
                                step_name,
                                compatibility_dimensions,
                            )
                            if analysis_result and not analysis_result.get("error"):
                                review_status = "pending"
                                if fixed_baseline and not _analysis_has_issues(
                                    analysis_result
                                ):
                                    review_status = "fixed"
                                baseline_id = upsert_compat_analysis_baseline(
                                    project_id=project_id,
                                    device_id=device_id,
                                    activity=activity,
                                    step_name=step_name,
                                    dom_hash=dom_hash,
                                    screenshot_hash=screenshot_hash,
                                    screenshot_body_hash=screenshot_body_hash,
                                    vlm_result=json.dumps(
                                        analysis_result, ensure_ascii=False
                                    ),
                                    screenshot_base64=screenshot_base64,
                                    source_parent_task_id=parent_task_id,
                                    source_child_task_id=child_task_id,
                                    review_status=review_status,
                                )
                                baseline_record = {
                                    "id": baseline_id,
                                    "review_status": review_status,
                                    "source_parent_task_id": parent_task_id,
                                    "source_child_task_id": child_task_id,
                                    "remark": "",
                                }
                            # 缓存 VLM 结果（若有 dom_hash）
                            if (
                                dom_hash
                                and analysis_result
                                and not analysis_result.get("error")
                            ):
                                cache_id = create_compat_vlm_cache(
                                    device_id,
                                    activity,
                                    step_name,
                                    dom_hash,
                                    json.dumps(analysis_result, ensure_ascii=False),
                                    screenshot_base64,
                                    json.dumps(compatibility_dimensions),
                                )
                                # 若 VLM 发现 issues，自动创建审核工单
                                issues = analysis_result.get("issues", [])
                                if not issues:
                                    for dim_result in analysis_result.get(
                                        "dimensions", []
                                    ):
                                        dim_issues = dim_result.get("issues", [])
                                        if dim_issues:
                                            issues.extend(dim_issues)
                                if issues:
                                    for issue in issues:
                                        create_audit_item(
                                            cache_id=cache_id,
                                            parent_task_id=parent_task_id,
                                            child_task_id=child_task_id,
                                            issue_type=issue.get("category", "unknown"),
                                            issue_detail=issue.get("description", ""),
                                            first_seen_task_id=parent_task_id,
                                        )

                        # 处理分析结果
                        if analysis_result:
                            # 只有请求超时或 JSON 解析失败才标记需人工审核
                            error_msg = analysis_result.get("error", "")
                            needs_manual = analysis_result.get(
                                "needs_manual_review", False
                            )
                            is_timeout_or_parse_fail = needs_manual and (
                                "超时" in error_msg
                                or "timeout" in error_msg.lower()
                                or "无法解析 JSON" in error_msg
                                or "JSON 解析失败" in error_msg
                                or "HTTP错误" in error_msg
                            )
                            if is_timeout_or_parse_fail:
                                logger.warning(
                                    f"[兼容性测试] VLM 请求超时或解析失败，标记人工审核: child_task_id={child_task_id}, step={step_name}, error={error_msg[:300]}"
                                )
                                status_val = "pending_review"
                                severity_val = "error"
                                msg = f"VLM 分析异常，需人工审核: {error_msg[:200]}"
                                evidence = {
                                    "vlm_analysis": analysis_result,
                                    "screenshot_base64": screenshot_base64,
                                    "analysis_state": analysis_state,
                                    "script_id": script_id,
                                    "script_name": script_name,
                                    "script_index": script_index,
                                }
                                if baseline_record:
                                    evidence.update(
                                        {
                                            "baseline_id": baseline_record.get("id"),
                                            "baseline_review_status": baseline_record.get(
                                                "review_status"
                                            ),
                                            "baseline_source_parent_task_id": baseline_record.get(
                                                "source_parent_task_id"
                                            ),
                                            "baseline_source_child_task_id": baseline_record.get(
                                                "source_child_task_id"
                                            ),
                                            "baseline_review_remark": baseline_record.get(
                                                "remark"
                                            )
                                            or "",
                                        }
                                    )
                                insert_task_event(
                                    task_id=child_task_id,
                                    parent_task_id=parent_task_id,
                                    event_type="assertion",
                                    dimension="layout",
                                    name=step_name,
                                    status=status_val,
                                    target=device_id,
                                    message=msg,
                                    severity=severity_val,
                                    step_index=idx + 1,
                                    evidence=evidence,
                                )
                                assertion_event = {
                                    "type": "assertion",
                                    "name": step_name,
                                    "dimension": "layout",
                                    "status": status_val,
                                    "message": msg,
                                    "step_index": idx + 1,
                                    "script_id": script_id,
                                    "script_name": script_name,
                                    "script_index": script_index,
                                    "evidence": evidence,
                                }
                                assertion_json_lines.append(assertion_event)
                                continue

                            assertion_summary["total"] += 1

                            # 根据分析结果更新断言状态
                            for dim_result in analysis_result.get("dimensions", []):
                                dim_name = dim_result.get("name", "")
                                dim_status = dim_result.get("status", "skipped")

                                if dim_name in assertion_summary["by_dimension"]:
                                    assertion_summary["by_dimension"][dim_name][
                                        "total"
                                    ] += 1
                                    if dim_status == "passed":
                                        assertion_summary["passed"] += 1
                                        assertion_summary["by_dimension"][dim_name][
                                            "passed"
                                        ] += 1
                                    elif dim_status == "failed":
                                        assertion_summary["failed"] += 1
                                        assertion_summary["by_dimension"][dim_name][
                                            "failed"
                                        ] += 1
                                    elif dim_status == "warning":
                                        assertion_summary["warning"] += 1
                                        assertion_summary["by_dimension"][dim_name][
                                            "warning"
                                        ] += 1
                                    else:
                                        assertion_summary["skipped"] += 1

                            # 获取issues列表（兼容不同格式）
                            issues = analysis_result.get("issues", [])
                            if not issues:
                                issues = []
                                for dim_result in analysis_result.get("dimensions", []):
                                    dim_issues = dim_result.get("issues", [])
                                    if dim_issues:
                                        issues.extend(dim_issues)

                            # 验证 issues 的 bbox 完整性
                            valid_bbox_count = sum(
                                1
                                for i in issues
                                if i.get("bbox") and len(i.get("bbox")) == 4
                            )
                            if issues and valid_bbox_count == 0:
                                logger.warning(
                                    f"[兼容性测试] VLM 返回 {len(issues)} 个 issues，但均缺少有效 bbox，无法标注"
                                )
                            elif issues and valid_bbox_count < len(issues):
                                logger.info(
                                    f"[兼容性测试] VLM 返回 {len(issues)} 个 issues，{valid_bbox_count} 个有 bbox，{len(issues) - valid_bbox_count} 个缺少 bbox"
                                )

                            # 当存在问题时，在截图上标注问题位置
                            annotated_screenshot_base64 = ""
                            if issues:
                                try:
                                    annotated_screenshot_base64 = (
                                        await asyncio.to_thread(
                                            annotate_base64,
                                            image_base64=screenshot_base64,
                                            issues=issues,
                                        )
                                    )
                                    if annotated_screenshot_base64:
                                        logger.debug(
                                            f"[兼容性测试] 截图已标注: step_name={step_name}, issues_count={len(issues)}"
                                        )
                                    else:
                                        logger.warning(
                                            f"[兼容性测试] 截图标注返回空: step_name={step_name}"
                                        )
                                except Exception as annotate_e:
                                    logger.error(
                                        f"[兼容性测试] 截图标注失败: step_name={step_name}, error={str(annotate_e)}"
                                    )
                            if (
                                annotated_screenshot_base64
                                and baseline_record
                                and baseline_record.get("id")
                            ):
                                update_compat_analysis_baseline_annotation(
                                    int(baseline_record["id"]),
                                    annotated_screenshot_base64,
                                )

                            # 确定 VLM 评估状态（VLM 返回 overall_assessment: "pass"|"warning"|"fail"）
                            vlm_assessment = analysis_result.get(
                                "overall_assessment", "pass"
                            )
                            vlm_confidence = analysis_result.get("confidence", 0.0)
                            vlm_issues = analysis_result.get("issues", [])
                            is_vlm_pass = vlm_assessment == "pass"
                            has_blocker = any(
                                i.get("severity") in ("blocker", "major")
                                for i in vlm_issues
                            )

                            if has_blocker:
                                status_val = "failed"
                                severity_val = "error"
                            elif vlm_assessment == "warning":
                                status_val = "warning"
                                severity_val = "warning"
                            else:
                                status_val = "passed"
                                severity_val = "info"

                            # 构造可读的消息
                            if vlm_issues:
                                issue_descs = [
                                    f"{i.get('category', '问题')}: {i.get('description', '')}"
                                    for i in vlm_issues
                                ]
                                msg = f"发现 {len(vlm_issues)} 个问题: {'; '.join(issue_descs)}"
                            elif is_vlm_pass:
                                msg = f"UI 兼容性结果: 建议 (置信度: {round(vlm_confidence * 100)}%)"
                            else:
                                msg = f"VLM 评估: {vlm_assessment} (置信度: {round(vlm_confidence * 100)}%)"

                            # 保存断言事件到数据库
                            evidence = {
                                "vlm_analysis": analysis_result,
                                "screenshot_base64": screenshot_base64,
                                "analysis_state": analysis_state,
                                "script_id": script_id,
                                "script_name": script_name,
                                "script_index": script_index,
                            }
                            if baseline_record:
                                evidence.update(
                                    {
                                        "baseline_id": baseline_record.get("id"),
                                        "baseline_review_status": baseline_record.get(
                                            "review_status"
                                        ),
                                        "baseline_source_parent_task_id": baseline_record.get(
                                            "source_parent_task_id"
                                        ),
                                        "baseline_source_child_task_id": baseline_record.get(
                                            "source_child_task_id"
                                        ),
                                        "baseline_review_remark": baseline_record.get(
                                            "remark"
                                        )
                                        or "",
                                    }
                                )
                            if annotated_screenshot_base64:
                                evidence["annotated_screenshot_base64"] = (
                                    annotated_screenshot_base64
                                )
                            elif baseline_record and baseline_record.get(
                                "annotated_screenshot_base64"
                            ):
                                evidence["annotated_screenshot_base64"] = (
                                    baseline_record.get("annotated_screenshot_base64")
                                )

                            insert_task_event(
                                task_id=child_task_id,
                                parent_task_id=parent_task_id,
                                event_type="assertion",
                                dimension="layout",
                                name=step_name,
                                status=status_val,
                                target=device_id,
                                message=msg,
                                severity=severity_val,
                                step_index=idx + 1,
                                evidence=evidence,
                            )

                            # 同时构造 JSON 行写回 output，供 HTML 报告生成器 _parse_vlm_assertion_steps 读取
                            assertion_event = {
                                "type": "assertion",
                                "name": step_name,
                                "dimension": "layout",
                                "status": status_val,
                                "message": msg,
                                "step_index": idx + 1,
                                "script_id": script_id,
                                "script_name": script_name,
                                "script_index": script_index,
                                "evidence": evidence,
                            }
                            assertion_json_lines.append(assertion_event)
                    else:
                        logger.warning(
                            f"[兼容性测试] 捕获事件缺少截图数据: child_task_id={child_task_id}, step={step_name}"
                        )

                except Exception as e:
                    logger.error(
                        f"[兼容性测试] 分析捕获失败: child_task_id={child_task_id}, step={step_name}, error={str(e)}"
                    )
                    assertion_summary["total"] += 1
                    assertion_summary["failed"] += 1
                    msg = f"VLM 分析失败，已跳过当前截图并继续后续流程: {str(e)[:200]}"
                    evidence = {
                        "screenshot_base64": capture_event.get("screenshot_base64"),
                        "analysis_state": "error",
                        "error": str(e),
                        "script_id": script_id,
                        "script_name": script_name,
                        "script_index": script_index,
                    }
                    insert_task_event(
                        task_id=child_task_id,
                        parent_task_id=parent_task_id,
                        event_type="assertion",
                        dimension="layout",
                        name=step_name,
                        status="pending_review",
                        target=device_id,
                        message=msg,
                        severity="error",
                        step_index=idx + 1,
                        evidence=evidence,
                    )
                    assertion_json_lines.append(
                        {
                            "type": "assertion",
                            "name": step_name,
                            "dimension": "layout",
                            "status": "pending_review",
                            "message": msg,
                            "step_index": idx + 1,
                            "script_id": script_id,
                            "script_name": script_name,
                            "script_index": script_index,
                            "evidence": evidence,
                        }
                    )
                    continue

            logger.debug(
                f"[兼容性测试] 批量 VLM 分析完成: child_task_id={child_task_id}, total={assertion_summary['total']}"
            )

            return {
                "assertion_summary": assertion_summary,
                "assertion_json_lines": assertion_json_lines,
            }
        except Exception as e:
            logger.error(
                f"[兼容性测试] 批量 VLM 分析异常: child_task_id={child_task_id}, error={str(e)}",
                exc_info=True,
            )
            return None

    async def _analyze_capture_with_limit(
        self,
        screenshot_base64: str,
        step_name: str,
        compatibility_dimensions: List[str],
    ) -> Dict[str, Any]:
        async with self._vlm_semaphore:
            return await asyncio.to_thread(
                self._analyze_single_capture,
                screenshot_base64,
                step_name,
                compatibility_dimensions,
            )

    @staticmethod
    def _analyze_single_capture(
        screenshot_base64: str, step_name: str, dimensions: List[str] = None
    ) -> Dict[str, Any]:
        """在线程池中执行单次 VLM 分析，避免阻塞事件循环"""
        from backend.compatibility.vlm_ui_analyzer import VLMUIAnalyzer

        analyzer = VLMUIAnalyzer()
        return analyzer.analyze_screen(
            screenshot_base64=screenshot_base64,
            context={"page_name": step_name},
            dimensions=dimensions,
        )

    def _execute_script_sync(self, script_content, device_id, env):
        """同步执行脚本"""
        import os
        import subprocess
        import sys
        import tempfile
        import time

        # 获取项目根目录
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        # 在 Windows 上，将反斜杠替换为正斜杠，避免路径问题
        project_root = project_root.replace("\\", "/")
        logger.info(f"[兼容性测试] project_root: {project_root}")

        # 设置 PYTHONPATH，确保 subprocess 能找到 backend 模块
        new_env = env.copy()
        current_pythonpath = new_env.get("PYTHONPATH", "")
        if current_pythonpath:
            new_env["PYTHONPATH"] = f"{project_root};{current_pythonpath}"
        else:
            new_env["PYTHONPATH"] = project_root
        logger.info(f"[兼容性测试] PYTHONPATH: {new_env.get('PYTHONPATH', '')}")

        # 在脚本开头添加调试信息
        debug_header = """
import sys
import os
print(f"[DEBUG] Python executable: {sys.executable}")
print(f"[DEBUG] PYTHONPATH: {os.environ.get('PYTHONPATH', 'NOT SET')}")
print(f"[DEBUG] sys.path: {sys.path}")
"""
        script_content_with_debug = debug_header + script_content

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(script_content_with_debug)
            tmpfile = f.name

        try:
            start_time = time.time()
            result = subprocess.run(
                [sys.executable, tmpfile],
                capture_output=True,
                text=True,
                timeout=300,
                env=new_env,
                encoding="utf-8",
                errors="replace",  # 替换无法解码的字符，避免UnicodeDecodeError
            )
            duration_ms = int((time.time() - start_time) * 1000)

            return {
                "success": result.returncode == 0,
                "output": result.stdout,
                "error": result.stderr,
                "returncode": result.returncode,
                "duration_ms": duration_ms,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "output": "",
                "error": "Script timed out",
                "returncode": -1,
                "duration_ms": 300000,
            }
        finally:
            import os

            if os.path.exists(tmpfile):
                try:
                    os.unlink(tmpfile)
                except OSError:
                    pass

    async def _handle_event(
        self, event_data, child_task_id, parent_task_id, device_id, platform, trace_id
    ):
        """处理事件"""
        event_type = event_data.get("type")

        if event_type == "assertion":
            # 保存到数据库
            insert_task_event(
                task_id=child_task_id,
                parent_task_id=parent_task_id,
                event_type="assertion",
                dimension=event_data.get("dimension"),
                name=event_data.get("name"),
                status=event_data.get("status"),
                target=event_data.get("target"),
                message=event_data.get("message"),
                severity=event_data.get("severity"),
                step_index=event_data.get("step_index"),
                evidence=event_data.get("evidence"),
            )

            await self._broadcast(
                {
                    "type": "compat_event",
                    "event": "assertion",
                    "parent_task_id": parent_task_id,
                    "child_task_id": child_task_id,
                    "device_id": device_id,
                    "platform": platform,
                    "trace_id": trace_id,
                    **event_data,
                }
            )

        elif event_type == "screenshot":
            # 保存截图路径到数据库
            insert_task_artifact(
                task_id=child_task_id,
                parent_task_id=parent_task_id,
                artifact_type="screenshot",
                relative_path=event_data.get("relative_path", ""),
                step_index=event_data.get("step_index"),
                assertion_name=event_data.get("assertion_name"),
            )

            await self._broadcast(
                {
                    "type": "compat_event",
                    "event": "screenshot_preview",
                    "parent_task_id": parent_task_id,
                    "child_task_id": child_task_id,
                    "path": event_data.get("relative_path", ""),
                    "kind": event_data.get("kind"),
                }
            )

        elif event_type == "artifact":
            insert_task_artifact(
                task_id=child_task_id,
                parent_task_id=parent_task_id,
                artifact_type=event_data.get("artifact_type", ""),
                relative_path=event_data.get("relative_path", ""),
                step_index=event_data.get("step_index"),
                assertion_name=event_data.get("assertion_name"),
            )

            await self._broadcast(
                {
                    "type": "compat_event",
                    "event": "artifact_created",
                    "parent_task_id": parent_task_id,
                    "child_task_id": child_task_id,
                    "artifact_type": event_data.get("artifact_type"),
                    "relative_path": event_data.get("relative_path"),
                }
            )

        elif event_type == "error":
            await self._broadcast(
                {
                    "type": "compat_event",
                    "event": "error",
                    "parent_task_id": parent_task_id,
                    "child_task_id": child_task_id,
                    "device_id": device_id,
                    "platform": platform,
                    **event_data,
                }
            )

        elif event_type == "capture":
            # 保存捕获事件到数据库（用于后续批量VLM分析）
            step_name = event_data.get("step_name", "")
            step_index = None
            if step_name:
                match = re.search(r"\d+", step_name)
                if match:
                    step_index = int(match.group())

            insert_task_artifact(
                task_id=child_task_id,
                parent_task_id=parent_task_id,
                artifact_type="capture_event",
                relative_path=json.dumps(event_data, ensure_ascii=False),
                step_index=step_index,
                assertion_name="",
            )

            await self._broadcast(
                {
                    "type": "compat_event",
                    "event": "capture_recorded",
                    "parent_task_id": parent_task_id,
                    "child_task_id": child_task_id,
                    "device_id": device_id,
                    "step_name": event_data.get("step_name"),
                    "timestamp": event_data.get("timestamp"),
                }
            )

        elif event_type == "step":
            await self._broadcast(
                {
                    "type": "compat_event",
                    "event": "compat_log",
                    "parent_task_id": parent_task_id,
                    "child_task_id": child_task_id,
                    "message": event_data.get("message"),
                    "step_index": event_data.get("index"),
                }
            )

    async def _aggregate_results(self, parent_task_id, results):
        """汇总执行结果"""
        summary = {
            "parent_task_id": parent_task_id,
            "total_devices": len(results),
            "finished_devices": sum(1 for r in results if r["status"] == "finished"),
            "failed_devices": sum(1 for r in results if r["status"] == "failed"),
            "assertion_total": sum(
                r.get("assertion_summary", {}).get("total", 0) for r in results
            ),
            "assertion_passed": sum(
                r.get("assertion_summary", {}).get("passed", 0) for r in results
            ),
            "assertion_failed": sum(
                r.get("assertion_summary", {}).get("failed", 0) for r in results
            ),
            "by_dimension": {},
            "device_results": results,
        }

        # 按维度汇总
        for dim in AssertionDimension:
            dim_total = 0
            dim_passed = 0
            dim_failed = 0

            for result in results:
                dim_data = (
                    result.get("assertion_summary", {})
                    .get("by_dimension", {})
                    .get(dim.value, {})
                )
                dim_total += dim_data.get("total", 0)
                dim_passed += dim_data.get("passed", 0)
                dim_failed += dim_data.get("failed", 0)

            summary["by_dimension"][dim.value] = {
                "total": dim_total,
                "passed": dim_passed,
                "failed": dim_failed,
            }

        return summary

    def _get_device_status(self, script_results: List[Dict[str, Any]]) -> str:
        statuses = [result.get("status", "failed") for result in script_results]
        if any(status == "failed" for status in statuses):
            return "failed"
        if any(status == "partial_failed" for status in statuses):
            return "partial_failed"
        if statuses and all(status == "finished" for status in statuses):
            return "finished"
        return "partial_failed"

    def _merge_assertion_summaries(
        self, script_results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        summary = {"total": 0, "passed": 0, "failed": 0, "warning": 0}
        by_dimension: Dict[str, Dict[str, int]] = {}
        for result in script_results:
            item = result.get("assertion_summary", {}) or {}
            summary["total"] += item.get("total", 0)
            summary["passed"] += item.get("passed", 0)
            summary["failed"] += item.get("failed", 0)
            summary["warning"] += item.get("warning", 0)
            for dimension, dim_data in (item.get("by_dimension", {}) or {}).items():
                target = by_dimension.setdefault(
                    dimension,
                    {"total": 0, "passed": 0, "failed": 0},
                )
                target["total"] += dim_data.get("total", 0)
                target["passed"] += dim_data.get("passed", 0)
                target["failed"] += dim_data.get("failed", 0)
        if by_dimension:
            summary["by_dimension"] = by_dimension
        return summary

    def _extract_key_issue_info(
        self, script_results: List[Dict[str, Any]]
    ) -> Optional[Dict[str, str]]:
        severity_rank = {
            "blocker": 0,
            "major": 1,
            "minor": 2,
            "warning": 2,
            "suggestion": 3,
        }
        best_issue: Optional[Dict[str, str]] = None

        for result in script_results:
            output = result.get("output")
            if not isinstance(output, str) or not output.strip():
                continue
            for line in output.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                if (
                    event.get("type") != "assertion"
                    and event.get("event_type") != "assertion"
                ):
                    continue

                evidence = event.get("evidence")
                if isinstance(evidence, str):
                    try:
                        evidence = json.loads(evidence)
                    except json.JSONDecodeError:
                        evidence = {}
                if not isinstance(evidence, dict):
                    evidence = {}

                fallback_issue = {
                    "severity": str(event.get("severity") or "suggestion").lower(),
                    "category": str(
                        event.get("dimension") or event.get("name") or "assertion"
                    ),
                    "description": str(event.get("message") or event.get("name") or ""),
                }

                vlm_analysis = evidence.get("vlm_analysis", {})
                vlm_issues = []
                if isinstance(vlm_analysis, dict):
                    raw_issues = vlm_analysis.get("issues", [])
                    if isinstance(raw_issues, list):
                        vlm_issues = [
                            issue for issue in raw_issues if isinstance(issue, dict)
                        ]

                issue_candidates = vlm_issues or [fallback_issue]
                for issue in issue_candidates:
                    candidate = {
                        "severity": str(
                            issue.get("severity") or fallback_issue["severity"]
                        ).lower(),
                        "category": str(
                            issue.get("category") or fallback_issue["category"]
                        ),
                        "description": str(
                            issue.get("description") or fallback_issue["description"]
                        ),
                    }
                    if not candidate["description"]:
                        continue
                    if best_issue is None or severity_rank.get(
                        candidate["severity"], 99
                    ) < severity_rank.get(best_issue["severity"], 99):
                        best_issue = candidate

        return best_issue

    def _extract_key_issue(self, script_results: List[Dict[str, Any]]) -> str:
        best_issue = self._extract_key_issue_info(script_results)
        if not best_issue:
            return ""
        return (
            f"[{best_issue['severity']}] "
            f"{best_issue['category']}: {best_issue['description']}"
        )

    def _group_results_by_device(
        self, results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        grouped: Dict[int, List[Dict[str, Any]]] = {}
        for result in results:
            child_task_id = result.get("child_task_id")
            if child_task_id is None:
                continue
            grouped.setdefault(child_task_id, []).append(result)

        device_results = []
        for child_task_id, script_results in grouped.items():
            first = script_results[0]
            key_issue = self._extract_key_issue_info(script_results)
            raw_device_id = first.get("device_id") or ""
            device_results.append(
                {
                    "parent_task_id": first.get("parent_task_id"),
                    "child_task_id": child_task_id,
                    "device_id": raw_device_id,
                    "device_label": _resolve_device_display_name(raw_device_id)
                    if raw_device_id
                    else "",
                    "platform": first.get("platform"),
                    "status": self._get_device_status(script_results),
                    "script_results": sorted(
                        script_results,
                        key=lambda item: int(item.get("script_index") or 0),
                    ),
                    "assertion_summary": self._merge_assertion_summaries(
                        script_results
                    ),
                    "key_issue": (
                        f"[{key_issue['severity']}] "
                        f"{key_issue['category']}: {key_issue['description']}"
                        if key_issue
                        else ""
                    ),
                    "key_issue_severity": key_issue.get("severity", "")
                    if key_issue
                    else "",
                }
            )
        return sorted(device_results, key=lambda item: str(item.get("device_id") or ""))

    def _get_child_assertions(
        self, events: List[Dict[str, Any]], child_task_id: int
    ) -> List[Dict[str, Any]]:
        return [
            event
            for event in events
            if event.get("event_type") == "assertion"
            and event.get("task_id") == child_task_id
        ]

    def _get_device_info_for_report(self, device_id: str) -> Optional[Dict[str, Any]]:
        """获取设备信息用于报告渲染。

        优先级：DB 库存 → mcp_tools 实时查询（含落库反哺）。
        缺失时返回 None，由 report_builder 兜底展示「未知」。
        """
        if not device_id:
            return None
        try:
            info = get_device_info(device_id)
            db_brand = (info or {}).get("brand") or ""
            db_model = (info or {}).get("model") or ""
            db_os_version = (info or {}).get("os_version") or ""
            if db_brand and db_model and db_os_version:
                return {
                    "brand": db_brand,
                    "model": db_model,
                    "os_version": db_os_version,
                    "platform": (info or {}).get("platform") or "",
                    "resolution": (info or {}).get("resolution"),
                    "theme": (info or {}).get("theme"),
                }
        except Exception:
            info = None

        # DB 缺字段时回退到实时设备信息，并把可拿到的字段落库
        try:
            from backend.mcp.mcp_tools import mcp_tools

            live = mcp_tools.get_device_info(device_id)
        except Exception:
            live = None

        if not live:
            return None

        live_brand = (getattr(live, "brand", "") or "").strip()
        live_model = (getattr(live, "model", "") or "").strip()
        live_version = (getattr(live, "version", "") or "").strip()
        if not live_model and not live_brand:
            return None

        # 反哺 DB（不抛错，避免污染主流程）
        try:
            upsert_device(
                device_id=device_id,
                brand=live_brand or None,
                model=live_model or None,
                os_version=live_version or None,
                platform=getattr(live, "platform", "Android") or "Android",
            )
        except Exception:
            pass

        return {
            "brand": live_brand or None,
            "model": live_model or None,
            "os_version": live_version or None,
            "resolution": None,
            "theme": None,
        }

    def _generate_child_report(
        self,
        parent_task: Dict[str, Any],
        child_task: Dict[str, Any],
        events: List[Dict[str, Any]],
    ) -> Optional[str]:
        assertions = self._get_child_assertions(events, child_task["id"])
        report_content = report_builder.generate_child_report(
            child_task_id=child_task["id"],
            parent_task_id=parent_task["id"],
            device_id=child_task["device_id"],
            platform=parent_task.get("platform", "Android"),
            status=child_task.get("status", "unknown"),
            assertions=assertions,
            device_info=self._get_device_info_for_report(child_task["device_id"]),
        )
        return report_builder.save_child_report(
            parent_task["id"], child_task["id"], report_content
        )

    def _generate_device_reports(self, parent_task_id: int) -> Dict[int, str]:
        parent_task = get_compat_parent_task(parent_task_id)
        if not parent_task:
            return {}

        child_tasks = get_compat_child_tasks(parent_task_id)
        events = get_task_events_by_parent(parent_task_id)

        report_paths: Dict[int, str] = {}
        for child_task in child_tasks:
            try:
                path = self._generate_child_report(parent_task, child_task, events)
                if path:
                    report_paths[child_task["id"]] = path
            except Exception as exc:
                logger.error(
                    "[兼容性测试] 子设备报告生成失败: parent_task_id=%s child_task_id=%s error=%s",
                    parent_task_id,
                    child_task.get("id"),
                    exc,
                    exc_info=True,
                )
        return report_paths

    async def _generate_report(self, parent_task_id, summary):
        """生成测试报告"""
        parent_task = get_compat_parent_task(parent_task_id)
        if not parent_task:
            return {}

        child_tasks = get_compat_child_tasks(parent_task_id)
        events = get_task_events_by_parent(parent_task_id)

        # 从父任务配置读取兼容性维度
        extra = parent_task.get("extra", "{}")
        if isinstance(extra, str) and extra:
            try:
                extra = json.loads(extra)
            except json.JSONDecodeError:
                extra = {}
        compatibility_dimensions = extra.get("compatibility_dimensions", [])

        # 从 VLM 分析问题的 category 重建 by_dimension（按真实维度聚合）
        by_dimension = {}
        for dim in compatibility_dimensions:
            by_dimension[dim] = {
                "total": 0,
                "blocker": 0,
                "major": 0,
                "minor": 0,
                "suggestion": 0,
            }
        blocker_count = 0
        major_count = 0
        minor_count = 0
        suggestion_count = 0
        for event in events:
            if event.get("event_type") != "assertion":
                continue
            ev = event.get("evidence", {})
            if isinstance(ev, str):
                try:
                    ev = json.loads(ev)
                except json.JSONDecodeError:
                    ev = {}
            vlm = ev.get("vlm_analysis", {}) if isinstance(ev, dict) else {}
            if not isinstance(vlm, dict):
                vlm = {}
            issues = vlm.get("issues", [])
            if isinstance(issues, list):
                for issue in issues:
                    if not isinstance(issue, dict):
                        continue
                    category = issue.get("category", "unknown")
                    if category not in by_dimension:
                        by_dimension[category] = {
                            "total": 0,
                            "blocker": 0,
                            "major": 0,
                            "minor": 0,
                            "suggestion": 0,
                        }
                    by_dimension[category]["total"] += 1
                    severity = str(issue.get("severity", "")).lower()
                    if severity == "blocker":
                        by_dimension[category]["blocker"] += 1
                        blocker_count += 1
                    elif severity == "major":
                        by_dimension[category]["major"] += 1
                        major_count += 1
                    elif severity == "minor":
                        by_dimension[category]["minor"] += 1
                        minor_count += 1
                    elif severity == "suggestion":
                        by_dimension[category]["suggestion"] += 1
                        suggestion_count += 1

        summary["by_dimension"] = by_dimension
        summary["blocker_count"] = blocker_count
        summary["major_count"] = major_count
        summary["minor_count"] = minor_count
        summary["suggestion_count"] = suggestion_count
        summary["check_total"] = (
            blocker_count + major_count + minor_count + suggestion_count
        )

        child_report_paths = self._generate_device_reports(parent_task_id)

        device_results = []
        for child_task in child_tasks:
            assertions = self._get_child_assertions(events, child_task["id"])
            device_info = self._get_device_info_for_report(child_task["device_id"])

            device_results.append(
                {
                    "parent_task_id": parent_task_id,
                    "device_id": child_task["device_id"],
                    "device_info": device_info,
                    "status": child_task["status"],
                    "assertions": assertions,
                    "child_task_id": child_task["id"],
                    "report_path": child_report_paths.get(child_task["id"]),
                }
            )

        report_content = report_builder.generate_parent_report(
            parent_task_id=parent_task_id,
            title=f"兼容性测试报告- {parent_task.get('name', '')}",
            platform=parent_task.get("platform", "Android"),
            status=parent_task.get("status", "unknown"),
            summary=summary,
            device_results=device_results,
        )
        report_builder.save_parent_report(parent_task_id, report_content)
        return child_report_paths

    async def get_compat_report(self, parent_task_id: int) -> Dict[str, Any]:
        """获取兼容性测试报告"""
        parent_task = get_compat_parent_task(parent_task_id)
        if not parent_task:
            raise ValueError(f"父任务不存在: {parent_task_id}")

        child_tasks = get_compat_child_tasks(parent_task_id)
        events = get_task_events_by_parent(parent_task_id)
        artifacts = get_task_artifacts_by_parent(parent_task_id)
        assertion_summary = get_assertion_summary(parent_task_id)

        # 按设备分组断言
        device_assertions = {}
        for event in events:
            if event["event_type"] == "assertion":
                device_id = None
                for child in child_tasks:
                    if child["id"] == event["task_id"]:
                        device_id = child["device_id"]
                        break

                if device_id not in device_assertions:
                    device_assertions[device_id] = []
                device_assertions[device_id].append(event)

        # 构建报告数据
        report_data = {
            "parent_task": parent_task,
            "child_tasks": child_tasks,
            "assertion_summary": assertion_summary,
            "device_assertions": device_assertions,
            "artifacts": artifacts,
            "events": events,
        }

        return report_data

    def cancel_compat_task(self, parent_task_id: int):
        """取消兼容性测试任务"""
        from backend.db.database import update_task

        # 释放设备锁
        device_lock_registry.release_all(str(parent_task_id))

        # 更新父任务状态
        update_task(
            parent_task_id,
            status="cancelled",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        # 更新所有子任务状态
        child_tasks = get_compat_child_tasks(parent_task_id)
        for child_task in child_tasks:
            update_task(
                child_task["id"],
                status="cancelled",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        # 广播取消事件
        asyncio.create_task(
            self._broadcast(
                {
                    "type": "compat_event",
                    "event": "parent_status",
                    "parent_task_id": parent_task_id,
                    "status": "cancelled",
                }
            )
        )


# 全局服务实例
compatibility_service = CompatibilityService()
