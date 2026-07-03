import asyncio
from typing import Any, Callable, Dict, List, Optional

from backend.agent.react_agent_integration import create_react_agent
from backend.llm import llm_protocols
from backend.mcp.mcp_tools import mcp_tools


class AgentManager:
    """Agent管理器 - 统一使用 ReAct Agent (LLM)"""

    def __init__(self):
        self.agent = None
        self.react_agent = None  # ReAct Agent 实例
        self.is_running = False
        self.log_callback = None
        self.current_task_id = None
        self._step_results: list[dict] = []
        self._use_react_mode = True
        self._max_steps = 20  # 最大执行步数
        self._cancelled = False  # 任务取消标志

    def init_agent(self) -> bool:
        """初始化Agent，验证LLM连通性"""
        if not llm_protocols.is_llm_initialized():
            return False

        try:
            test_msg = [{"role": "user", "content": "Respond with OK"}]
            response = llm_protocols.llm_protocol.chat_completion(test_msg)
            if response.startswith("Error:"):
                self.log(f"Agent预热失败: {response}")
                return False
        except Exception as e:
            self.log(f"Agent预热异常: {str(e)}")
            return False

        self.agent = True
        self.log("Agent 初始化成功，LLM 连通性验证通过")
        return True

    def auto_init_from_config(self) -> tuple[bool, str]:
        """从DB配置自动初始化Agent，返回(是否成功, 错误信息)"""
        if self.is_agent_initialized():
            return True, ""

        from backend.db.database import get_config

        # 加载LLM配置（用于ReAct Agent）
        llm_protocol = get_config("llm_protocol", "openapi")
        llm_base_url = get_config("llm_base_url", "")
        llm_apikey = get_config("llm_apikey", "")
        llm_model = get_config("llm_model", "")

        missing = []
        if not llm_base_url:
            missing.append("LLM基础URL")
        if not llm_apikey:
            missing.append("LLM API密钥")
        if not llm_model:
            missing.append("LLM模型名称")

        if missing:
            error_msg = f"LLM配置不完整，缺少: {', '.join(missing)}。请前往设置页面配置LLM API。"
            self.log(error_msg)
            return False, error_msg

        from backend.llm.llm_protocols import init_llm

        if init_llm(llm_protocol, llm_base_url, llm_apikey, llm_model):
            success = self.init_agent()
            if success:
                self.init_react_agent()
                return True, ""
            else:
                return False, "Agent初始化失败，请检查LLM连通性"
        else:
            return False, "LLM初始化失败，请检查API配置是否正确"

    def is_agent_initialized(self) -> bool:
        return self.agent is not None

    def init_react_agent(self) -> bool:
        """初始化 ReAct Agent"""
        if not llm_protocols.is_llm_initialized():
            return False

        try:
            self.react_agent = create_react_agent(
                llm_protocol=llm_protocols.llm_protocol,
                device_id=mcp_tools.current_device,
                log_callback=self.log_callback,
                max_steps=self._max_steps,
            )
            self.agent = True
            self.log("ReAct Agent 初始化成功")
            return True
        except Exception as e:
            self.log(f"ReAct Agent 创建失败: {str(e)}")
            self.react_agent = None
            return False

    def set_react_mode(self, enabled: bool):
        """设置是否使用 ReAct 模式（仅ReAct模式可用）"""
        self._use_react_mode = True
        if enabled and not self.react_agent:
            self.init_react_agent()

    def get_react_mode(self) -> bool:
        """获取当前是否使用 ReAct 模式"""
        return True

    def _current_script_device_type(self) -> str:
        platform = getattr(mcp_tools, "current_platform", "") or ""
        platform = platform.lower()
        if platform in {"harmony", "harmonyos", "hdc"}:
            return "hdc"
        if platform in {"ios", "wda"}:
            return "ios"
        return "adb"

    def set_max_steps(self, max_steps: int):
        """设置最大执行步数"""
        self._max_steps = max_steps
        if self.react_agent:
            self.react_agent.config.max_steps = max_steps

    def set_log_callback(self, callback: Callable[[str], None]):
        self.log_callback = callback
        # 如果 ReActAgent 已创建，更新其日志回调
        if self.react_agent:
            self.react_agent.log_callback = callback
            if getattr(self.react_agent, "mcp_tools", None):
                self.react_agent.mcp_tools.set_log_callback(callback)
            if getattr(self.react_agent, "llm_client", None):
                self.react_agent.llm_client.log_callback = callback

    def log(self, message: str):
        if self.log_callback:
            self.log_callback(message)

    async def execute_task(
        self, task_text: str, task_id: Optional[int] = None, test_type: str = "normal"
    ) -> Dict[str, Any]:
        """执行任务，使用 ReAct Agent (LLM)"""
        if not self.is_agent_initialized():
            success, error_msg = self.auto_init_from_config()
            if not success:
                return {"success": False, "error": error_msg}

        if not self.react_agent:
            return {"success": False, "error": "ReAct Agent not initialized"}

        return await self._execute_react_task(task_text, task_id, test_type)

    async def _execute_react_task(
        self, task_text: str, task_id: Optional[int] = None, test_type: str = "normal"
    ) -> Dict[str, Any]:
        """执行 ReAct Agent 任务"""
        if self.react_agent is None:
            # 尝试初始化 ReAct Agent
            if not self.init_react_agent():
                return {"success": False, "error": "ReAct Agent 初始化失败"}

        if not mcp_tools.current_device:
            return {"success": False, "error": "No device selected"}

        # 更新 ReAct Agent 的设备ID
        if self.react_agent.mcp_tools.device_id != mcp_tools.current_device:
            self.react_agent.mcp_tools.device_id = mcp_tools.current_device
            self.log(f"设备已切换: {mcp_tools.current_device}")

        self.is_running = True
        self.current_task_id = task_id
        self._step_results = []

        try:
            self.log(f"[ReAct模式] 开始执行任务: {task_text}")
            self.log(f"当前设备: {mcp_tools.current_device}")

            # 执行 ReAct Agent
            state = await asyncio.to_thread(self.react_agent.run, task_text, task_id)

            # 转换步骤结果
            for step_result in state.step_results:
                status = "passed" if step_result.success else "failed"
                action_str = ""

                if step_result.tool_calls:
                    for tool_call in step_result.tool_calls:
                        func = tool_call.get("function", {})
                        tool_name = func.get("name", "")
                        args = func.get("arguments", "{}")
                        action_str = f"{tool_name}({args})"

                self._step_results.append(
                    {
                        "status": status,
                        "action": action_str or f"Step {len(self._step_results) + 1}",
                        "log": step_result.thinking or "",
                        "screenshot": "",
                        "duration": None,
                    }
                )

            passed = sum(1 for s in self._step_results if s["status"] == "passed")
            failed = sum(1 for s in self._step_results if s["status"] == "failed")
            summary = {
                "total": len(self._step_results),
                "passed": passed,
                "failed": failed,
            }

            self.log(f"[ReAct模式] 任务完成: {state.message}")
            result = {
                "success": state.success,
                "result": state.message,
                "steps": self._step_results,
                "summary": summary,
            }
            from backend.utils.script_generator import (
                generate_script,
                generate_test_report,
            )

            try:
                step_results_raw = [
                    {
                        "step": sr.step,
                        "thinking": sr.thinking,
                        "tool_calls": sr.tool_calls,
                        "success": sr.success,
                    }
                    for sr in state.step_results
                ]
                
                # 生成页面变化记录（基于工具调用类型）
                page_changes = self._generate_page_changes_from_steps(step_results_raw, mcp_tools.current_device)
                
                # 性能优化：异步生成脚本和报告，避免阻塞事件循环
                device_type = self._current_script_device_type()
                
                # 使用线程池异步执行脚本生成
                script_content = await asyncio.to_thread(
                    generate_script,
                    task_text,
                    step_results_raw,
                    device_type,
                    False,  # use_llm=False，使用模板快速生成
                    True,   # with_compatibility_assertions
                    test_type,
                    page_changes,  # 传递页面变化记录
                )
                
                # 异步生成 HTML 测试报告
                report_content = await asyncio.to_thread(
                    generate_test_report,
                    task_text,
                    step_results_raw,
                    "com.lockin.loock",
                    0.0,
                    state.success,
                )
                result["script_preview"] = script_content
                result["test_report"] = report_content
                result["step_results_raw"] = step_results_raw
                result["page_changes"] = page_changes  # 返回页面变化记录
            except Exception:
                result["script_preview"] = None
                result["test_report"] = None
                result["step_results_raw"] = None
                result["page_changes"] = None

            if not state.success:
                result["error"] = state.message or "任务执行失败"
            return result
        except Exception as e:
            self.log(f"[ReAct模式] 执行任务时发生错误: {str(e)}")
            return {"success": False, "error": str(e)}
        finally:
            self.is_running = False
            self.current_task_id = None
    
    def _generate_page_changes_from_steps(
        self, step_results_raw: List[Dict[str, Any]], device_id: str
    ) -> List[Dict[str, Any]]:
        """从步骤结果生成页面变化记录（从执行时的hash记录中提取）
        
        Args:
            step_results_raw: 原始步骤结果
            device_id: 设备ID
        
        Returns:
            页面变化记录列表
        """
        page_changes = []
        
        for step_result in step_results_raw:
            thinking = step_result.get("thinking", "")
            tool_calls = step_result.get("tool_calls", [])
            tool_results = step_result.get("tool_results", [])
            
            # 获取操作名称和参数
            action_name = None
            action_args = {}
            
            if tool_calls:
                for tool_call in tool_calls:
                    func = tool_call.get("function", {})
                    action_name = func.get("name", "")
                    action_args = func.get("arguments", {})
                    break
            
            # 从工具结果中提取页面变化信息
            if tool_results and action_name:
                for tool_result in tool_results:
                    if isinstance(tool_result, dict) and "_page_change" in tool_result:
                        page_change_info = tool_result["_page_change"]
                        page_changes.append({
                            "action_name": action_name,
                            "action_args": action_args,
                            "thinking": thinking,
                            "before_hash": page_change_info.get("before_hash"),
                            "after_hash": page_change_info.get("after_hash"),
                            "page_changed": page_change_info.get("page_changed", False),
                        })
                        self.log(f"[页面变化] {action_name}: {page_change_info.get('before_hash')} -> {page_change_info.get('after_hash')}, 变化: {page_change_info.get('page_changed')}")
                        break
        
        return page_changes

    def cancel_task(self) -> bool:
        """取消当前任务"""
        if not self.is_running and not self._cancelled:
            return False

        self.log("任务已取消")
        self.is_running = False
        self._cancelled = True
        self.current_task_id = None

        # 停止 ReAct Agent — 设置 finished 标志中断循环
        if self.react_agent and self.react_agent.state and not self.react_agent.state.finished:
            self.react_agent.state.finished = True
            self.react_agent.state.success = False
            self.react_agent.state.message = "任务已中止"

        return True

    def get_status(self) -> Dict[str, Any]:
        return {
            "initialized": self.is_agent_initialized(),
            "running": self.is_running,
            "current_task_id": self.current_task_id,
            "current_device": mcp_tools.current_device,
        }


agent_manager = AgentManager()
