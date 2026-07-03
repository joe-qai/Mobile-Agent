"""调度器 - 设备锁、并行执行、事件解析、状态汇总"""
import asyncio
import json
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from backend.db.database import update_task
from backend.utils.python_executor import python_executor

from .assertions import AssertionStatus, ErrorCategory
from .device_lock import device_lock_registry
from .event_parser import EventParser, summarize_assertions


class CompatibilityScheduler:
    """兼容性测试调度器"""
    
    def __init__(self):
        self._running_tasks: Set[int] = set()
        self._websocket_callbacks: List[callable] = []
    
    def register_websocket_callback(self, callback: callable):
        """注册WebSocket回调"""
        self._websocket_callbacks.append(callback)
    
    def unregister_websocket_callback(self, callback: callable):
        """取消注册WebSocket回调"""
        if callback in self._websocket_callbacks:
            self._websocket_callbacks.remove(callback)
    
    async def _broadcast(self, event: Dict[str, Any]):
        """广播事件到所有WebSocket客户端"""
        for callback in self._websocket_callbacks[:]:
            try:
                await callback(event)
            except Exception:
                pass
    
    def _broadcast_sync(self, event: Dict[str, Any]):
        """同步广播事件"""
        for callback in self._websocket_callbacks[:]:
            try:
                asyncio.create_task(callback(event))
            except Exception:
                pass
    
    async def run_child_task(
        self,
        child_task_id: int,
        parent_task_id: int,
        script_content: str,
        device_id: str,
        platform: str,
        trace_id: str,
    ):
        """
        运行单个子任务
        
        Args:
            child_task_id: 子任务ID
            parent_task_id: 父任务ID
            script_content: 脚本内容
            device_id: 设备ID
            platform: 平台
            trace_id: 追踪ID
        """
        try:
            # 更新子任务状态为running
            update_task(child_task_id, status="running")
            self._broadcast_sync({
                "type": "compat_event",
                "event": "child_status",
                "parent_task_id": parent_task_id,
                "child_task_id": child_task_id,
                "platform": platform,
                "device_id": device_id,
                "status": "running",
            })
            
            # 注入兼容性环境变量
            env = {
                "COMPAT_PARENT_TASK_ID": str(parent_task_id),
                "COMPAT_CHILD_TASK_ID": str(child_task_id),
                "COMPAT_TRACE_ID": trace_id,
                "COMPAT_PLATFORM": platform,
                "DEVICE_ID": device_id,
            }
            
            # 执行脚本
            start_time = time.time()
            result = await asyncio.to_thread(
                self._execute_script, script_content, device_id, env
            )
            duration_ms = int((time.time() - start_time) * 1000)
            
            # 解析事件
            events = EventParser.parse_output(result["output"])
            
            # 汇总断言
            summary = summarize_assertions(events)
            
            # 处理每个事件
            for event_data in events:
                await self._handle_event(event_data, child_task_id, parent_task_id, device_id, platform, trace_id)
            
            # 确定子任务最终状态
            status = "finished" if result["success"] and summary.failed == 0 else "failed"
            
            # 更新子任务状态
            update_task(
                child_task_id,
                status=status,
                result=json.dumps({
                    "success": result["success"],
                    "assertion_summary": summary.to_dict(),
                    "duration_ms": duration_ms,
                }),
                log=result["output"],
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            
            # 广播子任务完成
            self._broadcast_sync({
                "type": "compat_event",
                "event": "child_status",
                "parent_task_id": parent_task_id,
                "child_task_id": child_task_id,
                "platform": platform,
                "device_id": device_id,
                "status": status,
                "assertion_summary": summary.to_dict(),
            })
            
            return {
                "child_task_id": child_task_id,
                "status": status,
                "summary": summary.to_dict(),
            }
        
        except Exception as e:
            error_msg = f"子任务执行异常 {str(e)}\n{traceback.format_exc()}"
            update_task(
                child_task_id,
                status="failed",
                result=json.dumps({"success": False, "error": error_msg}),
                log=error_msg,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            
            self._broadcast_sync({
                "type": "compat_event",
                "event": "error",
                "parent_task_id": parent_task_id,
                "child_task_id": child_task_id,
                "platform": platform,
                "device_id": device_id,
                "category": ErrorCategory.SCRIPT_ERROR.value,
                "message": error_msg,
            })
            
            return {
                "child_task_id": child_task_id,
                "status": "failed",
                "error": error_msg,
            }
    
    def _execute_script(self, script_content: str, device_id: str, env: Dict[str, str]) -> Dict:
        """执行脚本（同步方法）"""
        import os
        current_env = os.environ.copy()
        current_env.update(env)
        
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        project_root = project_root.replace("\\", "/")
        current_pythonpath = current_env.get("PYTHONPATH", "")
        if current_pythonpath:
            current_env["PYTHONPATH"] = f"{project_root};{current_pythonpath}"
        else:
            current_env["PYTHONPATH"] = project_root
        
        # 使用临时文件执行
        import subprocess
        import sys
        import tempfile
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(script_content)
            tmpfile = f.name
        
        try:
            result = subprocess.run(
                [sys.executable, tmpfile],
                capture_output=True,
                text=True,
                timeout=300,
                env=current_env,
                encoding="utf-8",
            )
            return {
                "success": result.returncode == 0,
                "output": result.stdout,
                "error": result.stderr if result.stderr else None,
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "output": "",
                "error": "Script timed out after 300s",
                "returncode": -1,
            }
        finally:
            if os.path.exists(tmpfile):
                try:
                    os.unlink(tmpfile)
                except OSError:
                    pass
    
    async def _handle_event(
        self,
        event_data: Dict[str, Any],
        child_task_id: int,
        parent_task_id: int,
        device_id: str,
        platform: str,
        trace_id: str,
    ):
        """处理单个事件"""
        event_type = event_data.get("type")
        
        if event_type == "assertion":
            assertion = EventParser.parse_assertion(event_data)
            if assertion:
                self._broadcast_sync({
                    "type": "compat_event",
                    "event": "assertion",
                    "parent_task_id": parent_task_id,
                    "child_task_id": child_task_id,
                    "platform": platform,
                    "device_id": device_id,
                    "trace_id": trace_id,
                    **assertion.to_dict(),
                })
        
        elif event_type == "step":
            step = EventParser.parse_step(event_data)
            if step:
                self._broadcast_sync({
                    "type": "compat_event",
                    "event": "compat_log",
                    "parent_task_id": parent_task_id,
                    "child_task_id": child_task_id,
                    "message": step.message,
                    "step_index": step.index,
                })
        
        elif event_type == "error":
            error_event = EventParser.parse_error(event_data)
            if error_event:
                self._broadcast_sync({
                    "type": "compat_event",
                    "event": "error",
                    "parent_task_id": parent_task_id,
                    "child_task_id": child_task_id,
                    "platform": platform,
                    "device_id": device_id,
                    **error_event.to_dict(),
                })
        
        elif event_type == "screenshot":
            screenshot = EventParser.parse_screenshot(event_data)
            if screenshot:
                self._broadcast_sync({
                    "type": "compat_event",
                    "event": "screenshot_preview",
                    "parent_task_id": parent_task_id,
                    "child_task_id": child_task_id,
                    "path": screenshot.get_effective_path(),
                    "kind": screenshot.kind,
                    "step_index": screenshot.step_index,
                })
        
        elif event_type == "artifact":
            artifact = EventParser.parse_artifact(event_data)
            if artifact:
                self._broadcast_sync({
                    "type": "compat_event",
                    "event": "artifact_created",
                    "parent_task_id": parent_task_id,
                    "child_task_id": child_task_id,
                    "artifact_type": artifact.artifact_type,
                    "relative_path": artifact.get_effective_path(),
                })
    
    async def run_parent_task(
        self,
        parent_task_id: int,
        script_content: str,
        device_ids: List[str],
        platform: str,
        trace_id: str,
    ) -> Dict[str, Any]:
        """
        运行父任务（包含多个设备子任务）
        
        Args:
            parent_task_id: 父任务ID
            script_content: 脚本内容
            device_ids: 设备ID列表
            platform: 平台
            trace_id: 追踪ID
        
        Returns:
            执行结果汇总
        """
        if parent_task_id in self._running_tasks:
            raise RuntimeError("任务已在运行中")
        
        self._running_tasks.add(parent_task_id)
        
        try:
            # 更新父任务状态为running
            update_task(parent_task_id, status="running")
            self._broadcast_sync({
                "type": "compat_event",
                "event": "parent_status",
                "parent_task_id": parent_task_id,
                "status": "running",
            })
            
            # 批量获取设备锁
            if not device_lock_registry.batch_acquire(device_ids, str(parent_task_id)):
                raise RuntimeError("无法获取设备锁，部分设备可能被占用")
            
            try:
                # 创建子任务执行任务列表
                tasks = []
                for idx, device_id in enumerate(device_ids):
                    child_task_id = parent_task_id * 100 + idx + 1  # 简单生成子任务ID
                    # 假设子任务已在数据库中创建，这里简化处理
                    task = asyncio.create_task(
                        self.run_child_task(
                            child_task_id=child_task_id,
                            parent_task_id=parent_task_id,
                            script_content=script_content,
                            device_id=device_id,
                            platform=platform,
                            trace_id=f"{trace_id}-{device_id}",
                        )
                    )
                    tasks.append(task)
                
                # 并行执行所有子任务
                results = await asyncio.gather(*tasks)
                
                # 汇总结
                summary = {
                    "parent_task_id": parent_task_id,
                    "total_devices": len(device_ids),
                    "finished_devices": sum(1 for r in results if r["status"] == "finished"),
                    "failed_devices": sum(1 for r in results if r["status"] == "failed"),
                    "assertion_total": sum(r.get("summary", {}).get("total", 0) for r in results),
                    "assertion_passed": sum(r.get("summary", {}).get("passed", 0) for r in results),
                    "assertion_failed": sum(r.get("summary", {}).get("failed", 0) for r in results),
                }
                
                # 确定父任务最终状态
                all_finished = all(r["status"] == "finished" for r in results)
                all_failed = all(r["status"] == "failed" for r in results)
                
                if all_finished:
                    parent_status = "finished"
                elif all_failed:
                    parent_status = "failed"
                else:
                    parent_status = "partial_failed"

                update_task(
                    parent_task_id,
                    status=parent_status,
                    result=json.dumps(summary),
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
                
                # 广播汇总
                self._broadcast_sync({
                    "type": "compat_event",
                    "event": "summary_updated",
                    "parent_task_id": parent_task_id,
                    "platform": platform,
                    "status": parent_status,
                    **summary,
                })
                
                return summary
            
            finally:
                # 释放设备锁
                device_lock_registry.release_all(str(parent_task_id))
        
        finally:
            self._running_tasks.discard(parent_task_id)
    
    def cancel_task(self, parent_task_id: int):
        """取消任务"""
        # 释放设备锁
        device_lock_registry.release_all(str(parent_task_id))
        
        # 更新父任务状态
        update_task(parent_task_id, status="cancelled", completed_at=datetime.now(timezone.utc).isoformat())
        
        self._broadcast_sync({
            "type": "compat_event",
            "event": "parent_status",
            "parent_task_id": parent_task_id,
            "status": "cancelled",
        })


# 全局调度器实例
compatibility_scheduler = CompatibilityScheduler()
