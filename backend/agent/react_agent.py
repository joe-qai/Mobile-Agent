"""ReAct Agent 实现 - 纯文本模式的 Agent"""

import asyncio
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from backend.agent.prompts import load_prompt
from backend.llm.llm_protocols import ReActAgentLLMClient
from backend.mcp.mcp_tools_base import MCPToolsBase, ScreenInfo, UIElement, UITreeResult
from backend.utils.tool_definitions import (
    SYSTEM_PROMPT,
    format_history,
)
from backend.utils.screenshot_collector import wait_until_scroll_idle, wait_until_stable

# 获取 logger
agent_logger = logging.getLogger("agent")

EMPTY_UI_TREE_FALLBACK_THRESHOLD = 3
ELEMENT_LOOKUP_FALLBACK_THRESHOLD = 3
STAGNATION_DETECTION_THRESHOLD = 5
MAX_CONSECUTIVE_FAILURES = 5

# 后台线程池（用于异步执行自我进化分析）
_evolution_executor = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="evolution_"
)


class TaskAnalyzer:
    """
    任务分析器 - 使用LLM智能分析任务是否属于Agent能力范围

    该模块负责：
    1. 使用LLM智能识别无关请求（如写文章、编程等非移动端任务）
    2. 判断任务是否需要特定应用支持
    3. 提供友好的拒绝响应

    相比硬编码规则的优势：
    - 更智能：利用LLM的理解能力，无需手动维护规则
    - 更灵活：支持多语言、复杂语义理解
    - 更易维护：规则集中在提示词中，便于更新
    - 更准确：基于语义理解而非简单模式匹配
    """

    CORE_CAPABILITIES = load_prompt("task_analyzer_capabilities.md")

    @classmethod
    def analyze_task(
        cls, task: str, llm_client: Optional[ReActAgentLLMClient] = None
    ) -> Dict[str, Any]:
        """
        使用LLM分析任务是否属于Agent能力范围

        Args:
            task: 用户任务描述
            llm_client: LLM客户端（可选，如果不提供则使用简单规则）

        Returns:
            Dict: 分析结果
                - can_handle: bool - 是否能处理
                - reason: str - 处理或拒绝的原因
                - suggestion: str - 建议（如果不能处理）
                - required_app: str - 需要的应用名称（如果需要）
                - task_type: str - 任务类型分类
        """
        # 如果有LLM客户端，使用智能分析
        if llm_client:
            return cls._analyze_with_llm(task, llm_client)
        else:
            # 降级到简单规则分析
            return cls._analyze_with_rules(task)

    @classmethod
    def _analyze_with_llm(
        cls, task: str, llm_client: ReActAgentLLMClient
    ) -> Dict[str, Any]:
        """使用LLM进行智能任务分析"""
        prompt_template = load_prompt("task_analyzer_prompt.md")
        analysis_prompt = prompt_template.format(core_capabilities=cls.CORE_CAPABILITIES, task=task)

        try:
            response = llm_client.chat(
                messages=[{"role": "user", "content": analysis_prompt}],
                system_prompt="你是一个任务分析助手，擅长判断任务是否属于移动端自动化Agent的能力范围。",
            )

            content = response.get("content", "").strip()

            # 提取JSON（处理可能的markdown代码块）
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            result = json.loads(content)
            result["is_rejected"] = not result.get("can_handle", False)
            return result

        except Exception:
            # LLM分析失败，降级到规则分析
            return cls._analyze_with_rules(task)

    @classmethod
    def _analyze_with_rules(cls, task: str) -> Dict[str, Any]:
        """使用简单规则进行任务分析（降级方案）"""
        task_lower = task.lower().strip()

        # 检查是否需要特定应用
        app_patterns = [
            (r"微信.*|公众号.*", "微信"),
            (r"抖音.*|短视频.*", "抖音"),
            (r"淘宝.*|购物.*", "淘宝"),
            (r"支付宝.*|支付.*", "支付宝"),
            (r"微博.*", "微博"),
            (r"QQ.*", "QQ"),
            (r"地图.*|导航.*", "地图应用"),
            (r"邮件.*|发邮件.*", "邮箱应用"),
            (r"浏览器.*|搜索.*", "浏览器"),
            (r"相机.*|拍照.*", "相机"),
            (r"^设置$|打开设置|进入设置|系统设置", "设置"),
        ]

        for pattern, app_name in app_patterns:
            if re.search(pattern, task_lower):
                return {
                    "can_handle": True,
                    "reason": f"任务需要使用「{app_name}」应用",
                    "suggestion": f"请确保设备已安装并可以正常访问「{app_name}」应用",
                    "required_app": app_name,
                    "task_type": "应用操作",
                    "is_rejected": False,
                }

        # 检查是否包含移动端操作相关词汇
        mobile_action_keywords = [
            "打开",
            "启动",
            "点击",
            "输入",
            "搜索",
            "滑动",
            "返回",
            "关闭",
            "安装",
            "卸载",
            "下载",
            "上传",
            "登录",
            "注册",
            "设置",
            "查看",
            "发送",
            "分享",
            "保存",
            "删除",
            "添加",
            "修改",
            "创建",
            "浏览",
        ]

        has_mobile_action = any(
            keyword in task_lower for keyword in mobile_action_keywords
        )

        if has_mobile_action:
            return {
                "can_handle": True,
                "reason": "任务包含移动端操作指令",
                "suggestion": "请确保设备已连接并正常工作",
                "required_app": None,
                "task_type": "设备操作",
                "is_rejected": False,
            }

        # 默认：无法确定的任务，尝试执行但给出警告
        return {
            "can_handle": True,
            "reason": "任务描述不够明确，将尝试执行",
            "suggestion": "建议提供更具体的移动端操作指令，例如：'打开微信并发送消息'",
            "required_app": None,
            "task_type": "未知",
            "is_rejected": False,
        }


@dataclass
class AgentConfig:
    """Agent 配置"""

    max_steps: int = 100
    max_tool_calls_per_step: int = 1
    verbose: bool = True
    stop_on_error: bool = True


@dataclass
class StepResult:
    """单步执行结果"""

    step: int
    thinking: str
    tool_calls: List[Dict[str, Any]]
    tool_results: List[Dict[str, Any]]
    success: bool
    error: Optional[str] = None


@dataclass
class AgentState:
    """Agent 状态"""

    task: str
    task_id: Optional[int] = None
    step_count: int = 0
    max_steps: int = 100  # 最大执行步数
    history: List[Dict[str, Any]] = field(default_factory=list)
    current_ui: Optional[UITreeResult] = None
    finished: bool = False
    success: bool = False
    message: str = ""
    step_results: List[StepResult] = field(default_factory=list)
    empty_ui_tree_count: int = 0
    element_lookup_failure_count: int = 0
    inferred_tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_call_history: List[List[Dict[str, Any]]] = field(
        default_factory=list
    )  # 最近几次的工具调用历史
    page_hash_history: List[str] = field(default_factory=list)  # 页面状态哈希历史
    consecutive_failure_count: int = 0  # 连续失败计数
    stagnation_count: int = 0  # 停滞计数（页面不变）


@dataclass
class PerceptionResult:
    """感知结果"""

    success: bool
    goal_achieved: bool
    action: str
    expected: str
    actual: Dict[str, Any]
    page_changed: bool
    anomalies: List[str]
    observations: List[str]
    recoverable: bool = True


class PerceptionEngine:
    """感知引擎 - 观察执行结果，判断是否达成目标"""

    def __init__(self):
        self.last_ui_tree: Optional[UITreeResult] = None
        self.last_action: Optional[str] = None
        self.last_result: Optional[Dict[str, Any]] = None

    def perceive(
        self, action: str, result: Dict[str, Any], current_ui: Optional[UITreeResult]
    ) -> PerceptionResult:
        """
        感知阶段：观察执行结果，判断是否达成目标

        Args:
            action: 执行的动作
            result: 执行结果
            current_ui: 当前 UI 树

        Returns:
            PerceptionResult: 感知结果
        """
        self.last_action = action
        self.last_result = result
        self.last_ui_tree = current_ui

        # 处理不同类型的返回结果
        if isinstance(result, dict):
            success = result.get("success", False)
        elif hasattr(result, "success"):
            success = result.success
        elif result is None:
            success = False
        else:
            # 非字典类型（如 UITreeResult）默认认为成功
            success = True

        # 确保 result 可以被 JSON 序列化
        actual_result = result.to_dict() if hasattr(result, 'to_dict') else result

        perception = PerceptionResult(
            success=success,
            goal_achieved=False,
            action=action,
            expected=self._get_expected_outcome(action),
            actual=actual_result,
            page_changed=self._check_page_change(current_ui),
            anomalies=[],
            observations=[],
        )

        if success:
            self._analyze_success(action, result, current_ui, perception)
        else:
            self._analyze_failure(action, result, perception)

        return perception

    def _get_expected_outcome(self, action: str) -> str:
        """获取预期结果"""
        expectations = {
            "launch_app": "应用启动成功，界面发生变化",
            "click_element": "元素被点击，页面可能跳转或出现新内容",
            "click_element_with_fallback": "元素被点击，使用了备选定位策略",
            "find_element": "元素存在于当前页面",
            "input_text": "文本被输入到元素中",
            "swipe": "屏幕发生滑动",
            "wait": "等待完成",
            "get_ui_tree": "获取到 UI 层次结构",
            "get_current_app": "获取到当前应用信息",
            "back": "返回上一页，页面可能变化",
            "home": "回到桌面",
            "long_press": "长按操作完成",
        }
        return expectations.get(action, "操作完成")

    def _check_page_change(self, current_ui: Optional[UITreeResult]) -> bool:
        """检查页面是否变化"""
        if not self.last_ui_tree or not current_ui:
            return True

        if len(self.last_ui_tree.elements) != len(current_ui.elements):
            return True

        for i, elem in enumerate(current_ui.elements):
            if i >= len(self.last_ui_tree.elements):
                return True
            last_elem = self.last_ui_tree.elements[i]
            if (
                elem.text != last_elem.text
                or elem.resource_id != last_elem.resource_id
                or elem.bounds != last_elem.bounds
            ):
                return True

        return False

    def _analyze_success(
        self,
        action: str,
        result: Dict[str, Any],
        current_ui: Optional[UITreeResult],
        perception: PerceptionResult,
    ):
        """分析成功结果"""
        perception.observations.append(f"{action} 执行成功")

        if action in ("click_element", "click_element_with_fallback"):
            # 点击操作后强制标记页面可能变化，因为点击可能触发异步加载、弹窗或页面跳转
            # 即使 UI 树看起来没变化，也应该强制刷新以获取最新状态
            perception.page_changed = True
            perception.observations.append("页面可能已跳转，将刷新 UI 状态")

        elif action == "launch_app":
            if isinstance(result, dict):
                app_info = result.get("current_app", {})
                if app_info:
                    pkg = app_info.get("current_app", "") or app_info.get(
                        "package_name", ""
                    )
                    perception.observations.append(f"当前应用: {pkg}")
                    if pkg:
                        perception.goal_achieved = True
            # 应用启动后，UI 状态已失效，需要标记页面变化
            perception.page_changed = True

        elif action == "find_element":
            # find_element 返回 UIElement 对象或 None
            if result is not None:
                perception.goal_achieved = True
                perception.observations.append("元素已找到")
                # 如果是 checkbox/switch 元素，报告其状态
                if hasattr(result, 'checked'):
                    status = "已选中" if result.checked else "未选中"
                    perception.observations.append(f"Checkbox/Switch 状态: {status}")

        elif action == "input_text":
            perception.goal_achieved = True
            # 检测安全键盘场景
            if current_ui:
                self._detect_secure_keyboard(current_ui, perception)

        elif action in ("back", "home"):
            perception.goal_achieved = True

        elif action == "swipe":
            perception.goal_achieved = True
            perception.page_changed = True
            perception.observations.append("屏幕已滑动，将刷新 UI 状态")

    def _detect_secure_keyboard(
        self, current_ui: Optional[UITreeResult], perception: PerceptionResult
    ):
        """
        检测安全键盘场景
        
        安全键盘特征：
        1. 键盘区域有特定的包名（如小米安全键盘：com.miui.securitykeyboard）
        2. 输入框显示占位符文本（如"请输入密码"）但实际已有内容
        3. 密码框显示为掩码字符（••••••••）
        """
        if not current_ui:
            return
        
        # 检测小米安全键盘
        secure_keyboard_packages = [
            "com.miui.securitykeyboard",
            "com.samsung.android.honeyboard",
            "com.huawei.inputmethod",
            "com.oppo.keyboard",
            "com.vivo.inputmethod",
        ]
        
        keyboard_detected = False
        for elem in current_ui.elements:
            if elem.package in secure_keyboard_packages:
                keyboard_detected = True
                perception.observations.append(f"检测到安全键盘: {elem.package}")
                break
        
        # 检测密码掩码（••••••••）
        for elem in current_ui.elements:
            text = elem.text or ""
            # 密码掩码通常是圆点或星号
            if text and all(c in "•*●■" for c in text):
                perception.observations.append(
                    f"密码已输入成功（显示为 {len(text)} 个掩码字符）"
                )
                # 标记这是一个关键里程碑
                perception.goal_achieved = True
                break
        
        # 如果检测到安全键盘但密码框显示占位符，说明输入可能成功
        if keyboard_detected:
            for elem in current_ui.elements:
                text = elem.text or ""
                # 占位符文本通常包含"请输入"、"密码"等关键词
                if "请输入密码" in text or "请输入" in text:
                    # 这可能是占位符，实际密码已输入
                    perception.observations.append(
                        "注意：输入框显示占位符文本，但密码可能已成功输入（安全键盘场景）"
                    )
                    break

    def _analyze_failure(
        self, action: str, result: Dict[str, Any], perception: PerceptionResult
    ):
        """分析失败结果"""
        if action == "find_element" and result is None:
            error = "元素未找到（返回 None）"
            perception.recoverable = True
            perception.observations.append(
                "⚠️ 元素未找到，请尝试：1) 使用 textContains 模糊匹配 2) 使用 resource-id 定位 3) 滚动页面后再试"
            )
        elif isinstance(result, dict):
            error = result.get("error") or result.get("message") or "未知错误"
            if "未找到元素" in error or "not found" in error.lower():
                perception.recoverable = True
                perception.observations.append("元素未找到，可能需要滚动或等待")
            elif "超时" in error or "timeout" in error.lower():
                perception.recoverable = True
                perception.observations.append("操作超时，可能需要重试")
            else:
                perception.observations.append(f"{action} 执行失败: {error}")
        else:
            error = str(result) if result else "未知错误"
            perception.observations.append(f"{action} 执行失败: {error}")
            return

        perception.anomalies.append(error)
        perception.observations.append(f"{action} 执行失败: {error}")


class ReActAgent:
    """
    ReAct Agent 实现

    基于 Prompt + LLM + MCP 工具调用的纯文本模式 Agent
    
    增强功能：
    - 智能状态感知：点击前检查 checkbox/switch 的当前状态
    - 记忆系统：记录成功/失败经验，支持持久化
    - 反馈学习：从过往经验中学习，避免重复错误
    - 任务状态跟踪：维护里程碑进度，避免忘记已完成的关键步骤
    """

    def __init__(
        self,
        llm_client: ReActAgentLLMClient,
        mcp_tools: MCPToolsBase,
        config: Optional[AgentConfig] = None,
        log_callback: Optional[Callable[[str], None]] = None,
        memory_manager: Optional["MemoryManager"] = None,
    ):
        self.llm_client = llm_client
        self.mcp_tools = mcp_tools
        self.config = config or AgentConfig()
        self.log_callback = log_callback
        self.state: Optional[AgentState] = None
        self.perception_engine = PerceptionEngine()
        
        # 任务状态跟踪器
        from backend.agent.task_state_tracker import TaskStateTracker
        self.task_tracker = TaskStateTracker()
        
        # 记忆系统：记录成功和失败的操作经验
        self.action_memory: Dict[str, Dict[str, Any]] = {
            "success": {},  # 成功经验
            "failure": {},  # 失败经验
        }
        
        # 持久化记忆管理器（可选）
        self.memory_manager = memory_manager
        
        # 持久化记忆管理器（新增）
        try:
            from backend.memory.persistent_memory import PersistentMemoryManager
            self.persistent_memory = PersistentMemoryManager()
        except Exception as e:
            agent_logger.warning(f"持久化记忆管理器初始化失败: {e}")
            self.persistent_memory = None
        
        # 任务级别的经验收集（用于任务结束后持久化）
        self.task_experiences: List[Dict[str, Any]] = []
        
        # 自我进化统计
        self.evolution_stats = {
            "strategies_optimized": 0,
            "failures_learned": 0,
            "patterns_recognized": 0
        }
        
        # 初始化反思学习组件
        self._init_reflection_components()
    
    def _init_reflection_components(self):
        """初始化反思学习组件（评估、分析、提炼）"""
        try:
            from backend.memory import MemoryManager
            from backend.reflection import ReactiveReflexor, ReflectionLoop
            from backend.reflection.analyzer import RootCauseAnalyzer
            from backend.reflection.distiller import KnowledgeDistiller
            from backend.reflection.evaluator import TaskEvaluator
            
            # 获取记忆管理器（如果已存在）
            memory_manager = getattr(self, 'memory_manager', None)
            short_term = memory_manager.short_term if memory_manager else None
            long_term = memory_manager.long_term if memory_manager else None
            
            # 初始化主动学习组件（实时评估、增量学习、失败预测）
            self.reactive_reflexor = ReactiveReflexor(
                short_term=short_term,
                long_term=long_term
            )
            
            # 初始化完整反思循环（任务结束后调用）
            self.reflection_loop = ReflectionLoop(
                evaluator=TaskEvaluator(),
                analyzer=RootCauseAnalyzer(),
                distiller=KnowledgeDistiller(),
                short_term=short_term,
                long_term=long_term
            )
            
            self.log("[反思组件] 反思学习组件初始化成功")
        except Exception as e:
            agent_logger.warning(f"反思学习组件初始化失败: {e}")
            self.reactive_reflexor = None
            self.reflection_loop = None
    
    def _get_reflection_context(self) -> Dict[str, Any]:
        """获取反思所需的上下文信息"""
        context = {
            "device_id": getattr(self.mcp_tools, "device_id", None),
            "task": self.state.task if self.state else None,
            "step_count": self.state.step_count if self.state else 0,
        }
        
        if hasattr(self.mcp_tools, '_current_app'):
            context["current_app"] = self.mcp_tools._current_app
        
        if self.state and self.state.current_ui:
            context["ui_elements_count"] = len(getattr(self.state.current_ui, 'elements', []))
        
        return context
    
    def _parse_sub_tasks(self, task: str) -> List[str]:
        """从任务描述中解析子任务列表"""
        if not task:
            return []
        
        separators = ["，", "，", "、", ";", ";", ",", "然后", "接着", "再"]
        sub_tasks = []
        current = task.strip()
        
        for sep in separators:
            parts = current.split(sep)
            if len(parts) > 1:
                sub_tasks = [p.strip() for p in parts if p.strip()]
                break
        
        if not sub_tasks:
            sub_tasks = [current]
        
        return sub_tasks

    def _build_task_log(self) -> Dict[str, Any]:
        """构建任务日志用于完整反思"""
        task = self.state.task if self.state else ""
        
        # 构建步骤列表
        steps = []
        if self.state and self.state.step_results:
            for sr in self.state.step_results:
                # 从 tool_calls 中提取操作描述
                description = ""
                if sr.tool_calls:
                    for tc in sr.tool_calls:
                        func = tc.get("function", {})
                        name = func.get("name", "")
                        args = func.get("arguments", {})
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except:
                                pass
                        if name == "launch_app":
                            app = args.get("app_name", args.get("package_name", ""))
                            description = f"打开应用: {app}"
                        elif name == "click_element":
                            by = args.get("by", "")
                            value = args.get("value", "")
                            description = f"点击元素: {by}={value}"
                        elif name == "input_text":
                            text = args.get("text", "")
                            description = f"输入文本: {text[:20]}"
                        elif name == "swipe":
                            direction = args.get("direction", "")
                            description = f"滑动: {direction}"
                        elif name == "find_element":
                            description = "查找元素"
                        elif name == "finish":
                            description = "任务完成"
                        else:
                            description = f"执行: {name}"
                
                step_entry = {
                    "step": sr.step,
                    "description": description or sr.thinking[:50],  # 添加 description 字段
                    "success": sr.success,
                    "error": sr.error,
                    "thinking": sr.thinking,
                    "tool_calls": sr.tool_calls,
                    "tool_results": sr.tool_results,
                    "retry_count": 0,  # 暂时固定为0，后续可从日志中统计
                }
                steps.append(step_entry)
        
        # 计算统计信息（供 evaluator.py 使用）
        total_steps = len(steps)
        success_count = sum(1 for s in steps if s.get("success", False))
        completed_steps = success_count  # 简化为成功步骤数
        
        task_log = {
            "task": task,
            "goal": task,  # 添加 goal 字段供 distiller 使用
            "task_id": self.state.task_id if self.state else None,
            "success": self.state.success if self.state else False,
            "message": self.state.message if self.state else "",
            "step_count": self.state.step_count if self.state else 0,
            "sub_tasks": self._parse_sub_tasks(task),
            "steps": steps,
            "environment": {},  # 环境信息，暂为空
            "summary": {
                "total_steps": total_steps,
                "success_count": success_count,
                "completed_steps": completed_steps,
                "is_completed": self.state.success if self.state else False,
            }
        }
        
        return task_log

    def log(self, message: str):
        """输出日志"""
        # 通过 logging 模块输出，供文件日志等使用
        agent_logger.info(f"[ReActAgent] {message}")
        # 通过 log_callback 发送到前端 WebSocket
        if self.log_callback:
            try:
                self.log_callback(f"[ReActAgent] {message}")
            except Exception:
                agent_logger.exception("Failed to send log via callback")
        # 如果配置了 verbose，也在控制台打印
        if self.config.verbose:
            print(f"[ReActAgent] {message}")

    def _init_task_tracker(self, task: str, analysis_result: Dict[str, Any]):
        """
        初始化任务状态跟踪器
        
        根据任务类型自动创建里程碑
        """
        # 重置跟踪器
        from backend.agent.task_state_tracker import TaskStateTracker
        self.task_tracker = TaskStateTracker()
        
        task_lower = task.lower()
        
        # 检测登录任务
        if any(kw in task_lower for kw in ["登录", "login", "密码", "password"]):
            self.task_tracker.use_template("login")
            self.log("[任务跟踪] 使用登录任务模板")
        
        # 检测其他任务类型...
        # 可以扩展更多模板
        
        # 记录任务上下文
        if analysis_result.get("required_app"):
            self.task_tracker.set_context("target_app", analysis_result["required_app"])
        
        self.task_tracker.set_context("original_task", task)

    def _update_milestone_from_action(
        self, 
        tool_name: str, 
        arguments: Dict[str, Any], 
        result: Any,
        perception: "PerceptionResult"
    ):
        """
        根据执行的操作更新里程碑状态
        
        将工具执行结果映射到里程碑完成状态
        """
        # 工具名称到里程碑的映射
        action_milestone_map = {
            "launch_app": "launch_app",
            "input_text": "input_password",  # 如果输入的是密码
            "click_element": None,  # 需要根据参数判断
        }
        
        # 检查是否是密码输入
        if tool_name == "input_text":
            text = arguments.get("text", "") if isinstance(arguments, dict) else ""
            # 简单判断：如果输入的文本看起来像密码（包含数字和字母）
            if text and any(c.isdigit() for c in text) and any(c.isalpha() for c in text):
                if self.task_tracker.is_milestone_completed("input_password"):
                    return  # 已经完成了
                self.task_tracker.complete_milestone("input_password", {
                    "password_length": len(text),
                    "masked_display": "••••••••"
                })
                self.log("[任务跟踪] 里程碑完成: 输入密码")
                progress = self.task_tracker.get_progress_summary()
                self.log(json.dumps({
                    "type": "progress",
                    "step": progress["completed"],
                    "total": progress["total_milestones"],
                    "percent": progress["progress_percent"]
                }))
        
        # 检查是否是启动应用
        elif tool_name == "launch_app":
            if self.task_tracker.is_milestone_completed("launch_app"):
                return
            app_name = arguments.get("app_name", "") if isinstance(arguments, dict) else ""
            self.task_tracker.complete_milestone("launch_app", {
                "app": app_name
            })
            self.log("[任务跟踪] 里程碑完成: 启动应用")
            progress = self.task_tracker.get_progress_summary()
            self.log(json.dumps({
                "type": "progress",
                "step": progress["completed"],
                "total": progress["total_milestones"],
                "percent": progress["progress_percent"]
            }))
        
        # 检查是否是点击登录按钮
        elif tool_name == "click_element":
            value = arguments.get("value", "") if isinstance(arguments, dict) else ""
            if "登录" in value or "login" in value.lower():
                if not self.task_tracker.is_milestone_completed("click_login"):
                    self.task_tracker.complete_milestone("click_login")
                    self.log("[任务跟踪] 里程碑完成: 点击登录")
                    progress = self.task_tracker.get_progress_summary()
                    self.log(json.dumps({
                        "type": "progress",
                        "step": progress["completed"],
                        "total": progress["total_milestones"],
                        "percent": progress["progress_percent"]
                    }))
            
            # 检查是否是同意用户协议
            elif "checkbox" in value.lower() or "协议" in value or "同意" in value:
                if not self.task_tracker.is_milestone_completed("agree_terms"):
                    self.task_tracker.complete_milestone("agree_terms")
                    self.log("[任务跟踪] 里程碑完成: 同意用户协议")
                    progress = self.task_tracker.get_progress_summary()
                    self.log(json.dumps({
                        "type": "progress",
                        "step": progress["completed"],
                        "total": progress["total_milestones"],
                        "percent": progress["progress_percent"]
                    }))
            
            # 检查是否是切换到密码登录
            elif "密码登录" in value:
                if not self.task_tracker.is_milestone_completed("switch_to_password"):
                    self.task_tracker.complete_milestone("switch_to_password")
                    self.log("[任务跟踪] 里程碑完成: 切换到密码登录")
                    progress = self.task_tracker.get_progress_summary()
                    self.log(json.dumps({
                        "type": "progress",
                        "step": progress["completed"],
                        "total": progress["total_milestones"],
                        "percent": progress["progress_percent"]
                    }))

    def _try_recover_from_loop(self, tool_name: str, step: int) -> bool:
        """
        尝试从死循环中恢复
        
        策略：
        1. 检查是否有已完成的里程碑
        2. 获取下一个应该执行的里程碑
        3. 跳过当前卡住的步骤，继续执行下一个里程碑
        
        Args:
            tool_name: 导致死循环的工具名称
            step: 当前步骤
        
        Returns:
            bool: 是否成功恢复
        """
        self.log(f"[恢复] 尝试从死循环恢复，卡住的工具: {tool_name}")
        
        # 获取任务进度
        progress = self.task_tracker.get_progress_summary()
        completed_count = progress["completed"]
        
        if completed_count == 0:
            # 没有完成任何里程碑，无法恢复
            self.log("[恢复] 没有已完成的里程碑，无法恢复")
            return False
        
        # 获取当前应该执行的里程碑
        current_milestone = self.task_tracker.get_current_milestone()
        next_milestone = self.task_tracker.get_next_milestone()
        
        if next_milestone:
            self.log(f"[恢复] 跳过卡住的步骤，尝试执行下一个里程碑: {next_milestone.name}")
            
            # 标记当前里程碑为失败（如果有的话）
            if current_milestone:
                self.task_tracker.fail_milestone(
                    current_milestone.id, 
                    f"死循环检测，跳过此里程碑"
                )
            
            # 在任务上下文中记录恢复信息
            self.task_tracker.set_context("recovery_step", step)
            self.task_tracker.set_context("recovery_from_loop", tool_name)
            
            return True
        
        # 没有下一个里程碑，检查是否所有里程碑都完成了
        if progress["progress_percent"] >= 80:
            self.log("[恢复] 大部分里程碑已完成，可以尝试结束任务")
            return True
        
        self.log("[恢复] 无法找到恢复路径")
        return False

    def _now_timestamp(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _clean_xml_tool_call(line: str) -> str:
        """将 LLM 输出的 XML 格式工具调用转为可读格式
        
        输入: [工具调用]: find_element<arg_key>by</arg_key><arg_value>text</arg_value>...
              或 find_element<arg_key>by</arg_key><arg_value>text</arg_value>...
        输出: [工具调用]: find_element(by=text, value=工作台, timeout=5)
        """
        import re
        prefix = ""
        rest = line
        m_prefix = re.match(r'^(\[.*?\]:\s*)', line)
        if m_prefix:
            prefix = m_prefix.group(1)
            rest = line[m_prefix.end():]
        m = re.match(r'^(\w+)', rest)
        if not m:
            return line
        tool_name = m.group(1)
        pairs = re.findall(r'<arg_key>([^<]+)</arg_key>\s*<arg_value>([^<]*)</arg_value>', rest)
        if not pairs:
            return re.sub(r'\s*</?\w+>.*$', '', line)
        params = ", ".join(f"{k}={v}" for k, v in pairs)
        return f"{prefix}{tool_name}({params})"

    def _extract_tool_calls_from_text(self, content: str) -> List[Dict[str, Any]]:
        """从 LLM 文本内容中提取工具调用，转为结构化 tool_calls

        支持两种格式：
        1. XML 格式: tool_name<param>value</param>...
        2. 括号格式: tool_name(key=value, key2=value2)

        Returns:
            结构化 tool_calls 列表，空列表表示没有找到
        """
        known_tools = {
            "launch_app", "click_element", "click_element_with_fallback",
            "input_text", "swipe", "find_element", "get_ui_tree",
            "get_current_app", "back", "home", "wait", "finish",
            "get_screen_size", "long_press", "press_element",
            "recent_apps", "tap_bounds", "click_text", "click_selector",
        }

        extracted = []

        for line in content.strip().split('\n'):
            line = line.strip()
            if not line:
                continue

            # 匹配 [工具调用]: tool_name(...) 或直接 tool_name<param>value</param>
            # 去掉 [思考]: / [工具调用]: 前缀
            m_prefix = re.match(r'^\[.*?\]:\s*', line)
            clean_line = line[m_prefix.end():] if m_prefix else line
            clean_line = re.sub(r'</?tool_call>\s*', '', clean_line).strip()

            # 提取工具名
            m_name = re.match(r'^(\w+)', clean_line)
            if not m_name:
                continue
            tool_name = m_name.group(1)

            # 只处理已知工具名
            if tool_name not in known_tools:
                continue

            # 优先尝试括号格式: tool_name(key=value, ...)
            m_paren = re.match(r'^(\w+)\((.*)\)\s*$', clean_line)
            if m_paren:
                args_str = m_paren.group(2)
                arguments = {}
                for pair in args_str.split(','):
                    pair = pair.strip()
                    m_kv = re.match(r'(\w+)\s*=\s*(.+)', pair)
                    if m_kv:
                        key = m_kv.group(1).strip()
                        val = m_kv.group(2).strip().strip('"\'')
                        try:
                            val = int(val)
                        except (ValueError, TypeError):
                            try:
                                val = float(val)
                            except (ValueError, TypeError):
                                pass
                        arguments[key] = val
                tool_call = {
                    "id": f"text-extracted-{len(extracted)}",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }
                extracted.append(tool_call)
                continue

            if clean_line == tool_name:
                tool_call = {
                    "id": f"text-extracted-{len(extracted)}",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps({}, ensure_ascii=False),
                    },
                }
                extracted.append(tool_call)
                continue

            # 尝试 XML 格式
            pairs = re.findall(r'<([^>]+)>([^<]*)</([^>]+)>', clean_line)
            # pairs 返回 (closing_tag, value, closing_tag_again) — 用第1和第2个元素
            if not pairs:
                continue

            arguments = {}
            pending_unwrap = []  # 收集 arg_key/arg_value 包装对
            for close_tag, value, _ in pairs:
                # 处理 arg_key/arg_value 包装格式
                # LLM 有时输出: ,arg_key>direction,arg_value>up — 需要解包
                if close_tag == "arg_key":
                    pending_unwrap.append(value)  # value 是参数名
                elif close_tag == "arg_value":
                    if pending_unwrap:
                        arguments[pending_unwrap.pop()] = value  # 解包为 key=value
                else:
                    arguments[close_tag] = value

            # 如果有未配对的 arg_key，保留原始格式
            for remaining_key in pending_unwrap:
                arguments[remaining_key] = ""

            # 构造结构化 tool_call
            tool_call = {
                "id": f"text-extracted-{len(extracted)}",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
            extracted.append(tool_call)

        return extracted

    def _record_success_experience(
        self, 
        tool_name: str, 
        arguments: Dict[str, Any], 
        result: Any,
        context: Optional[Dict[str, Any]] = None
    ):
        """
        记录成功操作经验
        
        Args:
            tool_name: 工具名称
            arguments: 工具参数
            result: 执行结果
            context: 额外上下文（如页面状态、元素状态等）
        """
        key = f"{tool_name}_{json.dumps(arguments, ensure_ascii=False)[:100]}"
        # 确保 arguments 是字典（LLM 可能返回 JSON 字符串）
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (json.JSONDecodeError, TypeError):
                arguments = {}
        experience = {
            "tool_name": tool_name,
            "arguments": arguments,
            "result": result,
            "timestamp": self._now_timestamp(),
            "count": self.action_memory["success"].get(key, {}).get("count", 0) + 1,
            "context": context or {},
        }
        self.action_memory["success"][key] = experience
        
        # 收集到任务经验列表（用于任务结束后持久化）
        self.task_experiences.append({
            "type": "success",
            "key": key,
            "experience": experience,
        })
        
        self.log(f"[记忆] 记录成功经验: {tool_name} (累计 {experience['count']} 次)")

    def _record_failure_experience(
        self, 
        tool_name: str, 
        arguments: Dict[str, Any], 
        result: Any,
        context: Optional[Dict[str, Any]] = None,
        lesson: Optional[str] = None
    ):
        """
        记录失败操作经验
        
        Args:
            tool_name: 工具名称
            arguments: 工具参数
            result: 执行结果
            context: 额外上下文
            lesson: 失败教训（如何避免）
        """
        key = f"{tool_name}_{json.dumps(arguments, ensure_ascii=False)[:100]}"
        # 确保 arguments 是字典（LLM 可能返回 JSON 字符串）
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (json.JSONDecodeError, TypeError):
                arguments = {}
        experience = {
            "tool_name": tool_name,
            "arguments": arguments,
            "result": result,
            "timestamp": self._now_timestamp(),
            "count": self.action_memory["failure"].get(key, {}).get("count", 0) + 1,
            "context": context or {},
            "lesson": lesson,
        }
        self.action_memory["failure"][key] = experience
        
        # 收集到任务经验列表
        self.task_experiences.append({
            "type": "failure",
            "key": key,
            "experience": experience,
        })
        
        self.log(f"[记忆] 记录失败经验: {tool_name} (累计 {experience['count']} 次)")
        if lesson:
            self.log(f"[记忆] 教训: {lesson}")
        
        # 记录自我进化统计（实际进化分析在任务结束后异步执行）
        self.evolution_stats["failures_learned"] += 1

    def _recall_experience(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        回忆过往操作经验
        
        优先级：
        1. 当前会话的成功经验
        2. 当前会话的失败经验（用于避免重复失败）
        3. 持久化记忆中的经验（如果启用了 MemoryManager）
        """
        key = f"{tool_name}_{json.dumps(arguments, ensure_ascii=False)[:100]}"
        
        # 优先返回成功经验
        if key in self.action_memory["success"]:
            exp = self.action_memory["success"][key]
            self.log(f"[记忆] 找到成功经验: {tool_name} (已成功 {exp['count']} 次)")
            return exp
        
        # 其次返回失败经验（用于避免重复失败）
        if key in self.action_memory["failure"]:
            exp = self.action_memory["failure"][key]
            self.log(f"[记忆] 找到失败经验: {tool_name} (已失败 {exp['count']} 次)")
            if exp.get("lesson"):
                self.log(f"[记忆] 过往教训: {exp['lesson']}")
            return exp
        
        # 尝试从持久化记忆中查询
        if self.persistent_memory:
            try:
                # 先查询成功经验
                success_exp = self.persistent_memory.query_experience(
                    tool_name, arguments, experience_type="success"
                )
                if success_exp:
                    self.log(f"[记忆] 从持久化记忆中找到成功经验: {tool_name} (历史成功 {success_exp['count']} 次)")
                    # 加载到当前会话记忆
                    self.action_memory["success"][key] = success_exp
                    return success_exp
                
                # 再查询失败经验
                failure_exp = self.persistent_memory.query_experience(
                    tool_name, arguments, experience_type="failure"
                )
                if failure_exp:
                    self.log(f"[记忆] 从持久化记忆中找到失败经验: {tool_name} (历史失败 {failure_exp['count']} 次)")
                    if failure_exp.get("lesson"):
                        self.log(f"[记忆] 历史教训: {failure_exp['lesson']}")
                    # 加载到当前会话记忆
                    self.action_memory["failure"][key] = failure_exp
                    return failure_exp
                
            except Exception as e:
                self.log(f"[记忆] 查询持久化记忆失败: {e}")
        
        return None
    
    def _query_similar_experiences(self, tool_name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从持久化记忆中查询相似经验"""
        if not self.persistent_memory:
            return []
        
        try:
            return self.persistent_memory.query_similar_experiences(tool_name, limit=5)
        except Exception as e:
            self.log(f"[记忆] 查询相似经验失败: {e}")
            return []
    
    def _evolve_from_failure(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        lesson: Optional[str],
        context: Optional[Dict[str, Any]]
    ):
        """
        从失败中学习并进化
        
        自我进化机制：
        1. 分析失败模式
        2. 提取可学习的教训
        3. 记录进化过程
        4. 可能的话，自动调整策略
        
        Args:
            tool_name: 失败的工具名称
            arguments: 工具参数
            lesson: 失败教训
            context: 失败上下文
        """
        if not self.persistent_memory:
            return
        
        try:
            # 1. 分析失败模式
            failure_pattern = self._analyze_failure_pattern(tool_name, arguments, lesson, context)
            
            # 2. 检查是否有类似的失败历史
            similar_failures = self.persistent_memory.query_similar_experiences(tool_name, limit=3)
            failure_count = len([f for f in similar_failures if f.get("experience_type") == "failure"])
            
            # 3. 如果同一操作失败次数达到阈值，触发策略优化
            if failure_count >= 3:
                self.log(f"[自我进化] 检测到重复失败模式: {tool_name} (失败 {failure_count} 次)")
                
                # 记录进化过程
                self.persistent_memory.log_evolution(
                    evolution_type="failure_pattern_recognition",
                    description=f"识别到 {tool_name} 的重复失败模式，已记录教训: {lesson}",
                    before_state={
                        "tool_name": tool_name,
                        "failure_count": failure_count
                    },
                    after_state={
                        "lesson": lesson,
                        "suggested_action": self._suggest_improvement(tool_name, lesson)
                    },
                    impact_score=0.7
                )
                
                self.evolution_stats["failures_learned"] += 1
                self.log(f"[自我进化] 已从失败中学习，累计学习 {self.evolution_stats['failures_learned']} 次")
            
            # 4. 如果有成功经验，对比成功和失败的差异
            success_experiences = [f for f in similar_failures if f.get("experience_type") == "success"]
            if success_experiences:
                self._compare_success_failure(tool_name, success_experiences[0], failure_pattern)
                
        except Exception as e:
            self.log(f"[自我进化] 进化失败: {e}")
    
    def _analyze_failure_pattern(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        lesson: Optional[str],
        context: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        分析失败模式
        
        Args:
            tool_name: 工具名称
            arguments: 工具参数
            lesson: 失败教训
            context: 失败上下文
        
        Returns:
            失败模式字典
        """
        pattern = {
            "tool_name": tool_name,
            "arguments": arguments,
            "lesson": lesson,
            "context": context,
            "timestamp": self._now_timestamp()
        }
        
        # 提取关键失败特征
        if tool_name == "click_element":
            by = arguments.get("by", "")
            value = arguments.get("value", "")
            pattern["failure_type"] = "element_not_found" if "未找到" in str(lesson) else "click_failed"
            pattern["element_selector"] = f"{by}={value}"
        
        elif tool_name == "input_text":
            pattern["failure_type"] = "input_failed"
            pattern["text_length"] = len(arguments.get("text", ""))
        
        return pattern
    
    def _suggest_improvement(self, tool_name: str, lesson: Optional[str]) -> str:
        """
        根据失败教训建议改进策略
        
        Args:
            tool_name: 工具名称
            lesson: 失败教训
        
        Returns:
            改进建议
        """
        suggestions = {
            "click_element": [
                "使用 textContains 模糊匹配",
                "滚动页面后再试",
                "等待元素出现后再点击",
                "使用 resource-id 定位",
                "使用坐标定位作为备选"
            ],
            "input_text": [
                "先点击输入框获取焦点",
                "清除输入框内容后再输入",
                "处理系统弹窗后再输入",
                "使用安全键盘输入"
            ]
        }
        
        tool_suggestions = suggestions.get(tool_name, ["重试操作"])
        
        # 根据具体失败原因选择建议
        if lesson and "未找到" in lesson:
            return tool_suggestions[0] if tool_suggestions else "重试操作"
        elif lesson and "超时" in lesson:
            return tool_suggestions[2] if len(tool_suggestions) > 2 else "重试操作"
        else:
            return tool_suggestions[0] if tool_suggestions else "重试操作"
    
    def _compare_success_failure(
        self,
        tool_name: str,
        success_exp: Dict[str, Any],
        failure_pattern: Dict[str, Any]
    ):
        """
        对比成功和失败的差异，提取关键因素
        
        Args:
            tool_name: 工具名称
            success_exp: 成功经验
            failure_pattern: 失败模式
        """
        self.log(f"[自我进化] 对比成功和失败经验: {tool_name}")
        
        # 提取差异
        differences = []
        
        # 对比参数
        success_args = success_exp.get("arguments", {})
        failure_args = failure_pattern.get("arguments", {})
        
        if success_args != failure_args:
            differences.append(f"参数不同: 成功={success_args}, 失败={failure_args}")
        
        # 对比上下文
        success_context = success_exp.get("context", {})
        failure_context = failure_pattern.get("context", {})
        
        if success_context.get("page_changed") != failure_context.get("page_changed"):
            differences.append(f"页面状态不同: 成功={success_context.get('page_changed')}, 失败={failure_context.get('page_changed')}")
        
        if differences:
            self.log(f"[自我进化] 发现关键差异: {'; '.join(differences)}")
            
            # 记录进化过程
            if self.persistent_memory:
                self.persistent_memory.log_evolution(
                    evolution_type="success_failure_comparison",
                    description=f"对比 {tool_name} 的成功和失败经验，发现差异",
                    before_state=failure_pattern,
                    after_state=success_exp,
                    impact_score=0.8
                )
                
                self.evolution_stats["patterns_recognized"] += 1
    
    def _save_task_experiences(self):
        """任务结束后保存经验到持久化记忆"""
        if not self.memory_manager or not self.task_experiences:
            return
        
        try:
            # 将任务经验保存到长期记忆
            for exp_record in self.task_experiences:
                exp_type = exp_record["type"]
                exp_data = exp_record["experience"]
                
                # 构建经验摘要
                summary = {
                    "tool_name": exp_data["tool_name"],
                    "arguments": exp_data["arguments"],
                    "success": exp_type == "success",
                    "count": exp_data["count"],
                    "lesson": exp_data.get("lesson"),
                    "timestamp": exp_data["timestamp"],
                }
                
                # 保存到 MemoryManager（具体实现取决于 MemoryManager 接口）
                # self.memory_manager.save_experience(summary)
                
            self.log(f"[记忆] 已保存 {len(self.task_experiences)} 条经验到持久化记忆")
        except Exception as e:
            self.log(f"[记忆] 保存经验失败: {e}")

    def _sanitize_event_payload(self, value: Any, max_chars: int = 2000) -> Any:
        if isinstance(value, dict):
            sanitized = {}
            for key, item in value.items():
                if key in {
                    "image_base64",
                    "screenshot_base64",
                    "screenshot",
                    "base64_data",
                    "xml",
                    "ui_xml",
                }:
                    sanitized[key] = f"<omitted:{key}>"
                else:
                    sanitized[key] = self._sanitize_event_payload(item, max_chars)
            return sanitized

        if isinstance(value, list):
            return [self._sanitize_event_payload(item, max_chars) for item in value[:50]]

        if hasattr(value, "to_dict"):
            return self._sanitize_event_payload(value.to_dict(), max_chars)

        if isinstance(value, str):
            if len(value) > max_chars:
                return {"text": value[:max_chars], "truncated": True}
            return value

        try:
            encoded = json.dumps(value, ensure_ascii=False)
        except TypeError:
            encoded = str(value)

        if len(encoded) > max_chars:
            return {"text": encoded[:max_chars], "truncated": True}
        return value

    def emit_event(
        self,
        event: str,
        payload: Dict[str, Any],
        step: Optional[int] = None,
        task_id: Optional[int] = None,
        source: str = "react_agent",
    ) -> None:
        if not self.log_callback:
            return

        # 事件去重：5秒内相同的事件不重复发送
        # 生成事件唯一标识（需要先处理 payload，防止 UITreeResult 等对象导致 JSON 序列化失败）
        sanitized_payload = self._sanitize_event_payload(payload)
        try:
            event_key = f"{event}_{step}_{json.dumps(sanitized_payload, ensure_ascii=False)[:200]}"
        except TypeError:
            # 兜底：如果序列化失败，使用事件名作为 key
            event_key = f"{event}_{step}"

        now = time.time()
        
        # 检查是否在去重窗口内
        if hasattr(self, '_last_event_time') and hasattr(self, '_last_event_key'):
            if now - self._last_event_time < 5.0 and self._last_event_key == event_key:
                # 5秒内相同事件，跳过
                return
        
        # 更新最后事件记录
        self._last_event_time = now
        self._last_event_key = event_key

        message = {
            "type": "react_event",
            "event": event,
            "task_id": task_id,
            "step": step,
            "timestamp": self._now_timestamp(),
            "source": source,
            "payload": self._sanitize_event_payload(payload),
        }
        try:
            self.log_callback(json.dumps(message, ensure_ascii=False))
        except Exception:
            agent_logger.exception("Failed to emit ReAct structured event")

    def _get_system_prompt(self) -> str:
        """获取系统提示（包含当前日期时间上下文）"""
        from datetime import datetime
        now = datetime.now()
        date_context = (
            f"\n# 当前日期时间\n"
            f"当前日期：{now.strftime('%Y-%m-%d')}\n"
            f"当前时间：{now.strftime('%H:%M')}\n"
            f"星期：{['一','二','三','四','五','六','日'][now.weekday()]}\n"
            f"请根据当前的日期，自行推理用户说的「昨天」「今天」「明天」分别对应哪一天。\n"
        )
        return SYSTEM_PROMPT + date_context

    def _normalize_ui_tree(self, ui_tree: Any) -> Any:
        """将兼容层返回的 dict UI 树归一化为 UITreeResult。"""
        if not isinstance(ui_tree, dict):
            return ui_tree
        if "elements" not in ui_tree and "screen_info" not in ui_tree:
            return ui_tree

        screen_info_data = ui_tree.get("screen_info") or {}
        screen_info = ScreenInfo()
        if isinstance(screen_info_data, dict):
            screen_info = ScreenInfo(
                width=screen_info_data.get("width", 1080),
                height=screen_info_data.get("height", 1920),
                current_app=screen_info_data.get("current_app", "") or "",
                current_activity=screen_info_data.get("current_activity", "") or "",
            )

        elements = []
        for item in ui_tree.get("elements") or []:
            if isinstance(item, UIElement):
                elements.append(item)
                continue
            if not isinstance(item, dict):
                continue
            elements.append(
                UIElement(
                    index=item.get("index", 0),
                    text=item.get("text", "") or "",
                    resource_id=(
                        item.get("resource_id", "")
                        or item.get("resource-id", "")
                        or ""
                    ),
                    content_desc=(
                        item.get("content_desc", "")
                        or item.get("content-desc", "")
                        or ""
                    ),
                    class_name=item.get("class_name", "") or item.get("class", "") or "",
                    package=item.get("package", "") or "",
                    bounds=item.get("bounds")
                    or {"left": 0, "top": 0, "right": 0, "bottom": 0},
                    clickable=bool(item.get("clickable", False)),
                    enabled=item.get("enabled", True),
                    focusable=bool(item.get("focusable", False)),
                    depth=item.get("depth", 0),
                    xpath=item.get("xpath", "") or "",
                )
            )

        return UITreeResult(
            elements=elements,
            screen_info=screen_info,
            raw_xml=ui_tree.get("raw_xml", "") or ui_tree.get("xml", "") or "",
        )

    def _get_normalized_ui_tree(self, force_refresh: bool = False) -> Any:
        return self._normalize_ui_tree(self.mcp_tools.get_ui_tree(force_refresh=force_refresh))

    def _ui_tree_to_state(self, ui_tree: Any) -> Any:
        normalized = self._normalize_ui_tree(ui_tree)
        if hasattr(normalized, "to_dict"):
            return normalized.to_dict()
        return normalized

    def _ui_tree_elements(self, ui_tree: Any) -> List[Any]:
        normalized = self._normalize_ui_tree(ui_tree)
        if hasattr(normalized, "elements"):
            return list(normalized.elements)
        if isinstance(normalized, dict):
            return list(normalized.get("elements") or [])
        return []

    def _ui_tree_element_count(self, ui_tree: Any) -> int:
        return len(self._ui_tree_elements(ui_tree))

    def _element_value(self, element: Any, *names: str, default: Any = "") -> Any:
        if isinstance(element, dict):
            for name in names:
                value = element.get(name)
                if value not in (None, ""):
                    return value
            return default
        for name in names:
            value = getattr(element, name, None)
            if value not in (None, ""):
                return value
        return default

    def _build_context(self) -> str:
        """构建上下文信息"""
        if not self.state.current_ui:
            return "（暂无 UI 状态）"

        normalized = self._normalize_ui_tree(self.state.current_ui)
        if hasattr(normalized, "format_text"):
            return normalized.format_text()

        return json.dumps(
            self._sanitize_event_payload(normalized),
            ensure_ascii=False,
            indent=2,
        )

    def _build_task_action_hint(self) -> str:
        inferred_calls = self.state.inferred_tool_calls

        if not inferred_calls:
            return "（无明确动作提示）"

        executed = set()
        for item in self.state.history:
            for tool_call in item.get("tool_calls", []):
                func = tool_call.get("function", {})
                args = func.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                executed.add(
                    (
                        func.get("name", ""),
                        json.dumps(args, ensure_ascii=False, sort_keys=True),
                    )
                )

        lines = []
        for call in inferred_calls:
            func = call.get("function", {})
            name = func.get("name", "")
            args = func.get("arguments", {})
            signature = (name, json.dumps(args, ensure_ascii=False, sort_keys=True))
            if signature in executed:
                continue
            lines.append(f"- {name}({args})")
        return "\n".join(lines) if lines else "（明确动作已执行）"

    def _build_messages(self) -> List[Dict[str, Any]]:
        """构建发送给 LLM 的消息列表"""
        messages = []

        history_text = format_history(self.state.history)
        context_text = self._build_context()
        action_hint = self._build_task_action_hint()
        
        # 添加任务进度信息
        progress_text = self.task_tracker.get_milestone_context_summary()
        
        # 查询长期记忆，获取相关策略和失败模式
        knowledge_text = self._get_knowledge_context()
        
        # 查询短期记忆，获取最近的类似失败
        recent_failure_text = self._get_recent_failure_context()
        
        # 获取本次会话的操作经验（成功模式加固 + 失败教训反馈）
        experience_text = self._get_session_experience_context()

        prompt_template = load_prompt("react_agent_user_prompt.md")
        user_prompt = prompt_template.format(
            task=self.state.task,
            progress_text=progress_text,
            context_text=context_text,
            action_hint=action_hint,
            history_text=history_text,
            knowledge_text=knowledge_text,
            recent_failure_text=recent_failure_text,
            experience_text=experience_text,
        )

        messages.append({"role": "user", "content": user_prompt})

        return messages
    
    def _get_knowledge_context(self) -> str:
        """查询长期记忆，获取相关策略和失败模式"""
        if not self.memory_manager or not self.memory_manager.long_term:
            return ""
        
        # 获取设备信息
        device_model = ""
        try:
            device_info = self.mcp_tools.get_device_info()
            if device_info:
                device_model = device_info.get("model", "")
        except:
            pass
        
        # 查询相关知识
        knowledge_prompt = self.memory_manager.long_term.get_relevant_knowledge_prompt(
            self.state.task, device_model
        )
        
        return knowledge_prompt
    
    def _get_recent_failure_context(self) -> str:
        """查询短期记忆，获取最近的类似失败"""
        if not self.memory_manager or not self.memory_manager.short_term:
            return ""
        
        # 获取最近的统计
        recent_stats = self.memory_manager.short_term.get_recent_stats(days=7)
        if recent_stats["total_tasks"] == 0:
            return ""
        
        parts = []
        parts.append(f"## 📊 最近执行经验")
        parts.append(f"最近7天: {recent_stats['total_tasks']}次任务, "
                     f"成功率{recent_stats['success_rate']:.0%}, "
                     f"平均重试{recent_stats['avg_retries']}次")
        
        return "\n".join(parts)

    def _get_session_experience_context(self) -> str:
        """获取本次会话的操作经验总结：成功模式加固 + 失败教训反馈"""
        has_success = bool(self.action_memory.get("success"))
        has_failure = bool(self.action_memory.get("failure"))
        if not has_success and not has_failure:
            return ""

        parts = []

        proven_patterns = []
        for key, exp in self.action_memory["success"].items():
            if exp.get("count", 0) >= 2:
                tool_name = exp.get("tool_name", "")
                args = exp.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                args_str = ", ".join(f"{k}={v}" for k, v in args.items())
                proven_patterns.append(
                    f"  - {tool_name}({args_str}) 已验证成功 {exp['count']} 次"
                )
        if proven_patterns:
            parts.append("### 已验证的成功模式（可继续沿用）")
            parts.extend(proven_patterns)

        failure_lessons = []
        for key, exp in self.action_memory["failure"].items():
            tool_name = exp.get("tool_name", "")
            args = exp.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    args = {}
            args_str = ", ".join(f"{k}={v}" for k, v in args.items())
            lesson = exp.get("lesson")
            if lesson:
                failure_lessons.append(f"  - {tool_name}({args_str}) 失败: {lesson}")
            else:
                failure_lessons.append(
                    f"  - {tool_name}({args_str}) 曾失败 {exp.get('count', 0)} 次，请更换策略"
                )
        if failure_lessons:
            parts.append("### 需避免的失败模式")
            parts.extend(failure_lessons)

        if parts:
            return "\n## 本次会话经验\n" + "\n".join(parts)
        return ""

    def _execute_tool_call(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """执行单个工具调用"""
        func = tool_call.get("function", {})
        tool_name = func.get("name", "")
        arguments_str = func.get("arguments", "{}")

        try:
            arguments = (
                json.loads(arguments_str)
                if isinstance(arguments_str, str)
                else arguments_str
            )
        except json.JSONDecodeError:
            return {"success": False, "error": f"参数解析失败: {arguments_str}"}

        self.log(f"[工具执行] {tool_name}({arguments})")

        if not hasattr(self.mcp_tools, tool_name):
            return {"success": False, "error": f"未知工具: {tool_name}"}

        # 判断是否是需要追踪页面变化的操作
        is_page_change_action = self._is_page_change_action(tool_name)
        before_hash = None
        after_hash = None
        
        if is_page_change_action:
            # 操作前截图并计算hash
            before_hash = self._capture_and_calculate_hash()
            self.log(f"[页面追踪] {tool_name} 操作前hash: {before_hash}")

        try:
            tool_method = getattr(self.mcp_tools, tool_name)
            result = tool_method(**arguments)
            
            # 操作后截图并计算hash
            if is_page_change_action:
                self._wait_after_page_action(tool_name)
                after_hash = self._capture_and_calculate_hash()
                page_changed = (before_hash and after_hash and before_hash != after_hash)
                
                # 将页面变化信息添加到结果中
                if isinstance(result, dict):
                    result["_page_change"] = {
                        "before_hash": before_hash,
                        "after_hash": after_hash,
                        "page_changed": page_changed
                    }
                
                self.log(f"[页面追踪] {tool_name} 操作后hash: {after_hash}, 页面变化: {page_changed}")
            
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _wait_after_page_action(self, tool_name: str) -> None:
        """等待页面动作后的渲染或滚动稳定。"""
        try:
            if tool_name in {
                "swipe",
                "swipe_points",
                "swipe_up",
                "swipe_down",
                "swipe_left",
                "swipe_right",
                "drag",
            }:
                result = wait_until_scroll_idle(self.mcp_tools)
                self.log(f"[同步] {tool_name} 滚动稳定等待: {result}")
                return

            result = wait_until_stable(self.mcp_tools)
            self.log(f"[同步] {tool_name} 页面稳定等待: {result}")
        except Exception as exc:
            self.log(f"[同步] {tool_name} 页面稳定等待失败，继续执行: {exc}")
    
    def _is_page_change_action(self, tool_name: str) -> bool:
        """判断操作是否可能导致页面变化"""
        page_change_actions = [
            "launch_app",
            "click_element",
            "click_text",
            "click_selector",
            "tap",
            "tap_bounds",
            "double_click",
            "drag",
            "swipe",
            "swipe_points",
            "swipe_up",
            "swipe_down",
            "swipe_left",
            "swipe_right",
            "clear_text",
            "back",
            "home",
            "recent_apps",
        ]
        return tool_name in page_change_actions
    
    def _capture_and_calculate_hash(self) -> Optional[str]:
        """截图并计算感知哈希"""
        try:
            from backend.utils.page_change_tracker import (
                calculate_phash,
                take_screenshot,
            )
            
            device_id = getattr(self.mcp_tools, 'device_id', None)
            if device_id is None:
                device_id = getattr(self.mcp_tools, 'current_device', None)
            
            self.log(f"[页面追踪] 使用设备ID: {device_id}")
            
            screenshot = take_screenshot(device_id)
            if screenshot:
                phash = calculate_phash(screenshot)
                self.log(f"[页面追踪] 截图成功，hash: {phash}")
                return phash
            else:
                self.log(f"[页面追踪] take_screenshot 返回 None")
        except Exception as e:
            self.log(f"[页面追踪] 截图计算hash失败: {type(e).__name__}: {e}")
        return None

    def _calculate_ui_hash(self) -> Optional[str]:
        """计算当前UI状态的哈希值（轻量级）"""
        if not self.state.current_ui:
            return None
        try:
            normalized = self._normalize_ui_tree(self.state.current_ui)
            if hasattr(normalized, 'to_dict'):
                ui_dict = normalized.to_dict()
                elements = ui_dict.get('elements', [])
                # 提取关键信息生成哈希：元素数量 + 主要文本 + 包名
                key_info = []
                for elem in elements[:20]:  # 只取前20个元素
                    text = elem.get('text', '')[:30] if isinstance(elem, dict) else ''
                    resource_id = elem.get('resource_id', '')[:50] if isinstance(elem, dict) else ''
                    class_name = elem.get('class_name', '')[:30] if isinstance(elem, dict) else ''
                    key_info.append(f"{text}:{resource_id}:{class_name}")
                screen_info = ui_dict.get('screen_info', {})
                current_app = screen_info.get('current_app', '')
                hash_str = f"{len(elements)}|{current_app}|{'|'.join(key_info)}"
                import hashlib
                return hashlib.md5(hash_str.encode('utf-8')).hexdigest()[:16]
        except Exception as e:
            self.log(f"[停滞检测] 计算UI哈希失败: {e}")
        return None

    def _detect_stagnation(self, step: int) -> Optional[Dict[str, Any]]:
        """
        检测任务停滞状态
        
        触发条件：
        1. 页面状态连续多次不变（无进展）
        2. 连续失败次数达到阈值
        3. 任务进度停滞（里程碑无变化）
        """
        current_hash = self._calculate_ui_hash()
        
        # 更新页面哈希历史
        if current_hash:
            self.state.page_hash_history.append(current_hash)
            # 只保留最近N次
            self.state.page_hash_history = self.state.page_hash_history[-STAGNATION_DETECTION_THRESHOLD:]
        
        # 检测条件1：页面状态连续多次不变
        if (
            len(self.state.page_hash_history) >= STAGNATION_DETECTION_THRESHOLD
            and len(set(self.state.page_hash_history)) == 1
        ):
            self.log(f"[停滞检测] 页面状态连续 {STAGNATION_DETECTION_THRESHOLD} 次不变，任务可能已完成或陷入僵局")
            
            # 检查任务进度，如果大部分里程碑已完成，则认为任务成功
            progress = self.task_tracker.get_progress_summary()
            if progress.get("progress_percent", 0) >= 80:
                self.log(f"[停滞检测] 任务进度已达 {progress['progress_percent']}%，页面状态稳定，判定任务完成")
                return {
                    "success": True,
                    "message": f"任务进度已达 {progress['progress_percent']}%，页面状态稳定，判定任务完成",
                }
            
            self.log(f"[停滞检测] 任务进度仅 {progress.get('progress_percent', 0)}%，页面状态停滞，终止任务")
            return {
                "success": False,
                "message": f"页面状态连续 {STAGNATION_DETECTION_THRESHOLD} 次不变，任务停滞，已终止",
            }
        
        # 检测条件2：连续失败次数达到阈值
        if self.state.consecutive_failure_count >= MAX_CONSECUTIVE_FAILURES:
            self.log(f"[停滞检测] 连续失败 {MAX_CONSECUTIVE_FAILURES} 次，任务无法继续")
            return {
                "success": False,
                "message": f"连续失败 {MAX_CONSECUTIVE_FAILURES} 次，任务无法继续执行",
            }
        
        return None

    def _parse_tool_arguments(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        func = tool_call.get("function", {})
        arguments = func.get("arguments", {})
        if isinstance(arguments, str):
            try:
                return json.loads(arguments)
            except json.JSONDecodeError:
                return {}
        if isinstance(arguments, dict):
            return arguments
        return {}

    def _is_success_result(self, result: Any) -> bool:
        if isinstance(result, dict):
            return result.get("success", False)
        return bool(result)

    def _summarize_tool_result(self, result: Any) -> str:
        if isinstance(result, dict):
            if "message" in result:
                return str(result.get("message"))
            if "error" in result:
                return str(result.get("error"))
            if "current_app" in result:
                return f"current_app={result.get('current_app')}"
            if "success" in result:
                return f"success={result.get('success')}"
        if hasattr(result, "elements"):
            return f"UI tree elements={len(getattr(result, 'elements', []))}"
        return type(result).__name__

    def _finish_step_result(
        self,
        started_at: float,
        step: int,
        thinking: str,
        tool_calls: List[Dict[str, Any]],
        tool_results: List[Dict[str, Any]],
        success: bool,
        error: Optional[str] = None,
    ) -> StepResult:
        step_result = StepResult(
            step=step,
            thinking=thinking,
            tool_calls=tool_calls,
            tool_results=tool_results,
            success=success,
            error=error,
        )
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        self.emit_event(
            "step_end",
            {
                "success": step_result.success,
                "duration_ms": duration_ms,
                "error": step_result.error,
            },
            step=step,
            task_id=getattr(self.state, "task_id", None),
        )
        return step_result

    def _format_bounds_value(self, bounds: Dict[str, int]) -> str:
        return (
            f"[{bounds.get('left', 0)},{bounds.get('top', 0)}]"
            f"[{bounds.get('right', 0)},{bounds.get('bottom', 0)}]"
        )

    def _format_click_trace_target(self, args: Dict[str, Any]) -> str:
        by = args.get("by", "")
        value = args.get("value", "")
        return f"by={by}, value={value}"

    def _log_click_trace_result(self, args: Dict[str, Any], result: Any) -> None:
        if not isinstance(result, dict):
            return
        if result.get("success"):
            if result.get("strategy") == "uiautomator2":
                selector = result.get("selector") or {}
                self.log(
                    "点击追踪: uiautomator2 已执行点击"
                    f"，selector={json.dumps(selector, ensure_ascii=False)}"
                )
            elif result.get("bounds"):
                self.log("点击追踪: bounds 点击已执行并成功")
            else:
                self.log("点击追踪: click_element 已执行并成功")
            return

        message = f"{result.get('message', '')} {result.get('error', '')}"
        if result.get("strategy") == "uiautomator2" or "uiautomator2" in message:
            selectors = result.get("attempted_selectors") or []
            selector_text = (
                f"，selectors={json.dumps(selectors, ensure_ascii=False)}"
                if selectors
                else ""
            )
            self.log(
                "点击追踪: uiautomator2 未找到元素，未执行点击"
                f" ({self._format_click_trace_target(args)}){selector_text}"
            )
        else:
            self.log(
                "点击追踪: click_element 未成功"
                f" ({self._format_click_trace_target(args)})，message={message.strip()}"
            )

    def _element_matches_target(self, elem: Any, by: str, value: str) -> bool:
        value_lower = (value or "").lower()
        if not value_lower:
            return False

        text = self._element_value(elem, "text")
        resource_id = self._element_value(elem, "resource_id", "resource-id")
        content_desc = self._element_value(elem, "content_desc", "content-desc")
        class_name = self._element_value(elem, "class_name", "class")
        xpath = self._element_value(elem, "xpath")

        if by == "text":
            return text == value or value_lower in text.lower()
        if by == "textContains":
            return value_lower in text.lower()
        if by in ("resource-id", "resourceId"):
            return resource_id == value
        if by in ("content-desc", "contentDescription"):
            return content_desc == value or value_lower in content_desc.lower()
        if by in ("class", "className"):
            return class_name == value
        if by == "xpath":
            return xpath == value

        return any(
            value_lower in field.lower()
            for field in (text, resource_id, content_desc, class_name, xpath)
            if field
        )

    def _find_click_target_in_ui_tree(
        self, ui_tree: UITreeResult, by: str, value: str
    ) -> Any:
        matches = [
            elem
            for elem in self._ui_tree_elements(ui_tree)
            if self._element_matches_target(elem, by, value)
        ]
        if not matches:
            return None
        matches.sort(
            key=lambda elem: (
                not bool(self._element_value(elem, "clickable", default=False)),
                not bool(self._element_value(elem, "enabled", default=True)),
                self._element_value(elem, "depth", default=0),
            )
        )
        return matches[0]

    def _recover_click_from_ui_tree(
        self, step: int, original_tool_call: Dict[str, Any]
    ) -> Dict[str, Any]:
        args = self._parse_tool_arguments(original_tool_call)
        target_by = args.get("by", "")
        target_value = args.get("value", "")
        recovery_tool_calls: List[Dict[str, Any]] = []
        recovery_tool_results: List[Dict[str, Any]] = []
        self.log("点击追踪: UI tree recovery 开始")

        self.log("[感知] click_element 直接点击失败，开始获取 UI 树分析页面元素")
        ui_tree_call = {
            "id": f"recovery-{step}-ui-tree",
            "function": {"name": "get_ui_tree", "arguments": "{}"},
        }
        ui_tree = self._get_normalized_ui_tree()
        self.state.current_ui = self._ui_tree_to_state(ui_tree)
        recovery_tool_calls.append(ui_tree_call)
        recovery_tool_results.append(
            self.llm_client.parse_tool_result(
                tool_call_id=ui_tree_call["id"],
                function_name="get_ui_tree",
                result=ui_tree,
            )
        )

        fallback_error = self._record_recovery_signal("get_ui_tree", ui_tree)
        if fallback_error:
            return {
                "success": False,
                "error": fallback_error,
                "tool_calls": recovery_tool_calls,
                "tool_results": recovery_tool_results,
            }

        target = self._find_click_target_in_ui_tree(ui_tree, target_by, target_value)
        if not target:
            self.log(f"点击追踪: UI tree 未命中 {target_value}，未执行 bounds 点击")
            return {
                "success": False,
                "error": f"UI 树中未找到自然语言操作对象: {target_value}",
                "tool_calls": recovery_tool_calls,
                "tool_results": recovery_tool_results,
            }

        bounds_value = self._format_bounds_value(
            self._element_value(target, "bounds", default={})
        )
        self.log(f"点击追踪: UI tree 命中 {target_value}，bounds={bounds_value}")
        self.log(
            f"[感知] UI 树命中目标 {target_value}，不自动执行 bounds 坐标点击"
        )
        return {
            "success": False,
            "error": (
                f"UI 树命中目标 {target_value}，bounds={bounds_value}；"
                "已跳过自动坐标点击，请改用语义定位或显式低级坐标操作"
            ),
            "tool_calls": recovery_tool_calls,
            "tool_results": recovery_tool_results,
        }

    def _classify_click_recovery_failure(self, error: str) -> Dict[str, Any]:
        """判断点击恢复失败是否应立即终止。"""
        if self.state.consecutive_failure_count >= MAX_CONSECUTIVE_FAILURES:
            return {"fatal": True, "allow_replan": False, "error": error}

        hard_failure_markers = [
            "设备",
            "连接",
            "UI 树连续为空",
            "无法获取 UI",
        ]
        if any(marker in error for marker in hard_failure_markers):
            return {"fatal": True, "allow_replan": False, "error": error}

        return {"fatal": False, "allow_replan": True, "error": error}

    def _is_element_not_found_result(self, result: Any) -> bool:
        if result is None:
            return True
        if not isinstance(result, dict):
            return False
        if result.get("success", True):
            return False
        message = f"{result.get('error', '')} {result.get('message', '')}".lower()
        return (
            "未找到元素" in message or "元素未找到" in message or "not found" in message
        )

    def _record_recovery_signal(self, tool_name: str, result: Any) -> Optional[str]:
        result = self._normalize_ui_tree(result)
        if isinstance(result, UITreeResult):
            if len(result.elements) == 0:
                self.state.empty_ui_tree_count += 1
                self.log(
                    f"[感知] UI 树为空 ({self.state.empty_ui_tree_count}/{EMPTY_UI_TREE_FALLBACK_THRESHOLD})"
                )
                if self.state.empty_ui_tree_count >= EMPTY_UI_TREE_FALLBACK_THRESHOLD:
                    return (
                        f"UI 树连续为空 {self.state.empty_ui_tree_count} 次"
                    )
            else:
                self.state.empty_ui_tree_count = 0
            return None

        if tool_name in (
            "find_element",
            "click_element",
            "click_element_with_fallback",
            "press_element",
        ):
            if self._is_element_not_found_result(result):
                self.state.element_lookup_failure_count += 1
                self.log(
                    "[感知] 元素定位失败 "
                    f"({self.state.element_lookup_failure_count}/{ELEMENT_LOOKUP_FALLBACK_THRESHOLD})"
                )
                if (
                    self.state.element_lookup_failure_count
                    >= ELEMENT_LOOKUP_FALLBACK_THRESHOLD
                ):
                    return (
                        f"元素定位连续失败 {self.state.element_lookup_failure_count} 次"
                    )
            else:
                self.state.element_lookup_failure_count = 0

        return None

    def _handle_finish(self, result: Dict[str, Any]) -> bool:
        """处理 finish 工具调用"""
        self.state.finished = True
        self.state.success = result.get("success", True)
        self.state.message = result.get("message", "任务完成")

        if self.state.success:
            self.log(f"[完成] 任务成功: {self.state.message}")
        else:
            self.log(f"[完成] 任务失败: {self.state.message}")

        return True

    def _think_before_action(self, tool_name: str, arguments: Any) -> Dict[str, Any]:
        """
        思考阶段：在执行操作前分析当前状态，决定是否执行

        ReAct 模式的核心：
        1. 感知：已经获取了 UI 状态
        2. 思考：分析当前状态，判断是否适合执行操作
        3. 决策：决定是否执行，或者需要先做其他操作

        Args:
            tool_name: 要执行的工具名称
            arguments: 工具参数（可能是字典或 JSON 字符串）

        Returns:
            Dict: 思考结果
                - skip_execution: bool - 是否跳过执行
                - reason: str - 原因说明
                - suggested_action: str - 建议的替代操作
        """
        result = {"skip_execution": False, "reason": "", "suggested_action": ""}

        # 确保 arguments 是字典格式
        if isinstance(arguments, str):
            try:
                import json
                arguments = json.loads(arguments)
            except (json.JSONDecodeError, TypeError):
                arguments = {}
        elif not isinstance(arguments, dict):
            arguments = {}

        # 1. 查询历史经验（记忆系统）
        past_experience = self._recall_experience(tool_name, arguments)
        if past_experience:
            # 如果之前失败过，检查是否需要调整策略
            if past_experience.get("lesson"):
                self.log(f"[记忆] 过往失败教训: {past_experience['lesson']}")
                # 可以在这里添加策略调整逻辑

            # 如果之前成功过，可以复用经验
            if past_experience.get("count", 0) > 0 and past_experience.get("result", {}).get("success"):
                self.log(f"[记忆] 该操作之前已成功 {past_experience['count']} 次，可以继续执行")
        
        # 对于 click_element 操作，如果有 UI 树则检查目标元素是否存在
        if tool_name == "click_element":
            # 如果没有 UI 树，主动获取 UI 树来验证元素是否存在
            current_elements = self._ui_tree_elements(self.state.current_ui)
            if not self.state.current_ui or len(current_elements) == 0:
                self.log(f"[思考] click_element 操作需要验证元素，获取 UI 树...")
                ui_tree_start = time.perf_counter()
                ui_tree = self._get_ui_tree_with_retry(max_retries=3, delay_ms=1000)
                ui_tree_duration_ms = int((time.perf_counter() - ui_tree_start) * 1000)
                self.state.current_ui = self._ui_tree_to_state(ui_tree)
                element_count = self._ui_tree_element_count(ui_tree)
                self.log(f"[性能] click_element 前获取 UI 树耗时: {ui_tree_duration_ms}ms, 元素数: {element_count}")
            
            current_elements = self._ui_tree_elements(self.state.current_ui)
            if self.state.current_ui and len(current_elements) > 0:
                by = arguments.get("by", "")
                value = arguments.get("value", "")
                
                # 在 UI 树中查找目标元素
                target_element = self._find_element_in_ui_tree(by, value)
                
                if not target_element:
                    result["skip_execution"] = True
                    result["reason"] = f"页面上未找到目标元素: {by}={value}"
                    result["suggested_action"] = "scroll_or_wait"
                    return result
                
                # 检查元素当前状态（智能决策）
                element_state = self._analyze_element_state(target_element)
                if element_state.get("is_checkable") and element_state.get("state_description"):
                    # checkbox/switch 等可选中元素
                    current_checked = element_state.get("checked", False)
                    self.log(f"[思考] 元素 {by}={value} 是可选中元素，当前状态: {'已选中' if current_checked else '未选中'}")

                    # 如果目标已经是期望状态，跳过点击
                    # 注意：对于 checkbox，通常点击会切换状态，所以如果已选中则不需要再点击
                    if current_checked:
                        result["skip_execution"] = True
                        result["reason"] = f"元素 {by}={value} 已经处于选中状态，无需重复点击"
                        result["suggested_action"] = "proceed_next"
                        self.log(f"[思考] 元素已选中，跳过点击操作")

                        # 记录成功经验：避免重复操作
                        self._record_success_experience(
                            tool_name,
                            arguments,
                            {"success": True, "skipped": True, "reason": result["reason"]},
                            context={"element_state": element_state}
                        )
                        return result

                    # 记录操作前的状态（用于后续验证）
                    result["pre_action_state"] = {
                        "checked": current_checked,
                        "element_description": f"{by}={value}"
                    }
                    # 保存到实例变量，供后续记录经验时使用
                    self._current_pre_action_state = result["pre_action_state"]

                # 元素存在且状态检查通过，可以执行
                self.log(f"[思考] 找到目标元素: {by}={value}，准备执行点击")
        
        # 对于 input_text 操作，检查输入框是否存在并选择正确的输入框
        elif tool_name == "input_text":
            text_to_input = arguments.get("text", "")
            
            # 如果没有 UI 树，主动获取 UI 树来找到正确的输入框
            current_elements = self._ui_tree_elements(self.state.current_ui)
            if not self.state.current_ui or len(current_elements) == 0:
                self.log(f"[思考] input_text 操作需要定位输入框，获取 UI 树...")
                ui_tree_start = time.perf_counter()
                ui_tree = self._get_ui_tree_with_retry(max_retries=3, delay_ms=1000)
                ui_tree_duration_ms = int((time.perf_counter() - ui_tree_start) * 1000)
                self.state.current_ui = self._ui_tree_to_state(ui_tree)
                element_count = self._ui_tree_element_count(ui_tree)
                self.log(f"[性能] input_text 前获取 UI 树耗时: {ui_tree_duration_ms}ms, 元素数: {element_count}")
            
            # 如果有 UI 树，则查找输入框并尝试点击正确的输入框
            current_elements = self._ui_tree_elements(self.state.current_ui)
            if self.state.current_ui and len(current_elements) > 0:
                # 查找输入框
                input_boxes = self._find_input_boxes()
                
                if input_boxes:
                    # 根据输入内容选择合适的输入框
                    target_box = self._select_input_box(input_boxes, text_to_input)
                    
                    if target_box:
                        # 需要先点击输入框使其获得焦点
                        # 优先使用 resource_id 定位，其次使用 bounds 中心坐标
                        resource_id = self._element_value(
                            target_box, "resource_id", "resource-id"
                        )
                        bounds = self._element_value(target_box, "bounds", default={})
                        
                        click_result = None
                        if resource_id and resource_id != "":
                            # 尝试使用 resourceId 定位
                            self.log(f"[思考] 使用 resourceId 定位目标输入框: {resource_id}")
                            click_result = self.mcp_tools.click_element(
                                by="resourceId", 
                                value=resource_id
                            )
                        elif bounds and "left" in bounds:
                            # 使用 bounds 中心坐标定位
                            center_x = (bounds["left"] + bounds["right"]) // 2
                            center_y = (bounds["top"] + bounds["bottom"]) // 2
                            self.log(f"[思考] 使用坐标定位目标输入框: ({center_x}, {center_y})")
                            click_result = self.mcp_tools.click_position(center_x, center_y)
                        
                        if click_result and click_result.get("success"):
                            self.log(f"[感知] 已点击目标输入框，准备输入文本")
                        else:
                            self.log(f"[感知] 点击输入框失败，将直接在当前焦点输入")
                    else:
                        self.log("[思考] 未找到合适的输入框，将在当前焦点输入框中输入")
                else:
                    self.log("[思考] 页面上未找到输入框元素，直接执行输入")
            else:
                self.log("[思考] 没有 UI 树信息，直接执行输入操作")
        
        return result
    
    def _analyze_element_state(self, element: Any) -> Dict[str, Any]:
        """
        分析元素当前状态（智能感知）
        
        检测元素的状态属性：
        - checked: checkbox/switch 的选中状态
        - enabled: 元素是否可用
        - clickable: 元素是否可点击
        - focusable: 元素是否可聚焦
        - selected: 元素是否被选中
        
        Args:
            element: UI 树中的元素对象
            
        Returns:
            Dict: 元素状态信息
        """
        state = {
            "checked": False,
            "enabled": True,
            "clickable": True,
            "focusable": False,
            "selected": False,
            "is_checkable": False,
            "state_description": "",
        }
        
        if not element:
            return state
        
        # 提取元素属性
        checked = self._element_value(element, "checked", default="false")
        enabled = self._element_value(element, "enabled", default="true")
        clickable = self._element_value(element, "clickable", default="true")
        checkable = self._element_value(element, "checkable", default="false")
        focusable = self._element_value(element, "focusable", default="false")
        selected = self._element_value(element, "selected", default="false")
        
        # 转换为布尔值
        def to_bool(val) -> bool:
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.lower() == "true"
            return bool(val)
        
        state["checked"] = to_bool(checked)
        state["enabled"] = to_bool(enabled)
        state["clickable"] = to_bool(clickable)
        state["is_checkable"] = to_bool(checkable)
        state["focusable"] = to_bool(focusable)
        state["selected"] = to_bool(selected)
        
        # 生成状态描述
        if state["is_checkable"]:
            state["state_description"] = "已选中" if state["checked"] else "未选中"
        
        return state
    
    def _find_element_in_ui_tree(self, by: str, value: str) -> Optional[Any]:
        """在 UI 树中查找元素"""
        if not self.state.current_ui:
            return None
        
        value_lower = (value or "").lower()
        if not value_lower:
            return None
        
        elements = self._ui_tree_elements(self.state.current_ui)
        
        for elem in elements:
            text = self._element_value(elem, "text")
            resource_id = self._element_value(elem, "resource_id", "resource-id")
            content_desc = self._element_value(elem, "content_desc", "content-desc")
            
            if by == "text":
                if text == value or value_lower in text.lower():
                    return elem
            elif by == "textContains":
                if value_lower in text.lower():
                    return elem
            elif by in ("resource-id", "resourceId"):
                if resource_id == value:
                    return elem
            elif by in ("content-desc", "contentDescription"):
                if content_desc == value or value_lower in content_desc.lower():
                    return elem
            else:
                # 默认匹配：检查所有字段
                if any(value_lower in field.lower() for field in [text, resource_id, content_desc] if field):
                    return elem
        
        return None
    
    def _prepare_input_text_context(self, step: int) -> None:
        """
        在执行 input_text 操作前准备上下文：获取 UI 状态并定位正确的输入框
        
        注意：此方法已废弃，逻辑已移至 _think_before_action
        """
        # 此方法已废弃，不再执行任何操作
        pass

    def _get_ui_tree_with_retry(self, max_retries: int = 3, delay_ms: int = 1000) -> Any:
        """获取 UI 树，支持重试机制
        
        Args:
            max_retries: 最大重试次数
            delay_ms: 重试间隔（毫秒）
            
        Returns:
            UITreeResult: UI 树结果
        """
        import time
        
        for attempt in range(max_retries):
            ui_tree = self._get_normalized_ui_tree()
            
            # 检查是否获取到有效元素
            element_count = self._ui_tree_element_count(ui_tree)
            
            if element_count > 0:
                self.log(f"[感知] 第 {attempt + 1} 次尝试成功获取 UI 树，包含 {element_count} 个元素")
                return ui_tree
            
            self.log(f"[感知] 第 {attempt + 1} 次尝试获取 UI 树失败，元素数为 0")
            
            # 如果不是最后一次尝试，等待后重试
            if attempt < max_retries - 1:
                self.log(f"[感知] 等待 {delay_ms}ms 后重试...")
                time.sleep(delay_ms / 1000)
        
        # 返回最后一次获取的结果（即使是空的）
        return ui_tree
    
    def _find_input_boxes(self) -> List[Any]:
        """查找页面上所有的输入框元素"""
        if not self.state.current_ui:
            return []
        
        input_boxes = []
        elements = self._ui_tree_elements(self.state.current_ui)
        for elem in elements:
            # 检查是否是输入框
            class_name = self._element_value(elem, "class_name", "class")
            resource_id = self._element_value(elem, "resource_id", "resource-id")
            text = self._element_value(elem, "text")
            hint = self._element_value(elem, "hint")
            content_desc = self._element_value(elem, "content_desc", "content-desc")
            
            # 判断是否是输入框的特征
            is_edit_text = "EditText" in class_name or "TextField" in class_name
            
            # 判断是否是密码输入框的特征
            is_password = (
                "password" in resource_id.lower() or
                "password" in hint.lower() or
                "密码" in text or
                "密码" in hint or
                "密码" in content_desc
            )
            
            # 判断是否是手机号输入框的特征
            is_phone = (
                "phone" in resource_id.lower() or
                "mobile" in resource_id.lower() or
                "手机" in hint or
                "手机号" in hint or
                "+86" in hint or
                "账号" in hint or
                "account" in hint.lower()
            )
            
            if is_edit_text or "请输入" in text or "请输入" in hint:
                input_boxes.append(elem)
        
        return input_boxes
    
    def _select_input_box(self, input_boxes: List[Any], text: str) -> Any:
        """
        根据输入内容选择合适的输入框
        
        策略：
        1. 如果输入内容看起来像密码（包含特殊字符、数字等），优先选择密码输入框
        2. 如果输入内容看起来像手机号，优先选择手机号输入框
        3. 如果输入内容看起来像验证码，优先选择验证码输入框
        4. 否则选择普通输入框
        """
        if not input_boxes:
            self.log(f"[思考] 没有找到任何输入框")
            return None
        
        # 判断输入内容类型
        is_password_like = (
            len(text) >= 6 and
            any(c.isupper() for c in text) and
            any(c.isdigit() for c in text) and
            any(c in "!@#$%^&*()_+-=[]{}|;':\",./<>?" for c in text)
        )
        
        is_phone_like = (
            text.isdigit() and
            len(text) >= 8 and
            len(text) <= 15
        )
        
        is_verification_code = (
            text.isdigit() and
            len(text) >= 4 and
            len(text) <= 8
        )
        
        self.log(f"[思考] 输入内容分析: 密码={is_password_like}, 手机号={is_phone_like}, 验证码={is_verification_code}")
        self.log(f"[思考] 找到 {len(input_boxes)} 个输入框")
        for i, box in enumerate(input_boxes):
            hint = self._element_value(box, "hint")
            box_text = self._element_value(box, "text")
            resource_id = self._element_value(box, "resource_id", "resource-id")
            self.log(f"[思考] 输入框[{i}]: hint='{hint}', text='{box_text}', id='{resource_id}'")
        
        # 根据输入类型选择对应的输入框
        for i, box in enumerate(input_boxes):
            hint = self._element_value(box, "hint")
            box_text = self._element_value(box, "text")
            resource_id = self._element_value(box, "resource_id", "resource-id")
            content_desc = self._element_value(box, "content_desc", "content-desc")
            
            all_text = f"{hint} {box_text} {resource_id} {content_desc}".lower()
            
            if is_password_like:
                # 优先选择密码输入框 - 排除手机号输入框
                if ("密码" in all_text or "password" in all_text or
                    "pass" in all_text or "pwd" in all_text or
                    "请输入密码" in hint or "请输入密码" in box_text):
                    # 排除手机号输入框
                    if "+86" in hint or "手机" in hint or "手机号" in hint:
                        self.log(f"[思考] 输入框[{i}] 可能是手机号输入框，跳过")
                        continue
                    self.log(f"[思考] 找到密码输入框: 输入框[{i}], hint={hint}, text={box_text}")
                    return box
            
            elif is_phone_like:
                # 优先选择手机号输入框
                if ("手机" in all_text or "phone" in all_text or
                    "手机号" in all_text or "电话" in all_text or
                    "账号" in all_text or "account" in all_text or
                    "用户名" in all_text or "username" in all_text or
                    "+86" in hint or "+86" in box_text):
                    self.log(f"[思考] 找到手机号输入框: 输入框[{i}], hint={hint}, text={box_text}")
                    return box
            
            elif is_verification_code:
                # 优先选择验证码输入框
                if ("验证码" in all_text or "code" in all_text or
                    "验证" in all_text or "verification" in all_text):
                    self.log(f"[思考] 找到验证码输入框: 输入框[{i}], hint={hint}, text={box_text}")
                    return box
        
        # 如果没有找到匹配的输入框，使用默认策略
        if is_password_like:
            # 密码通常对应第二个输入框（账号是第一个，密码是第二个）
            # 但首先尝试找提示文字包含"密码"的输入框
            for i, box in enumerate(input_boxes):
                box_hint = self._element_value(box, "hint")
                box_text = self._element_value(box, "text")
                # 排除手机号输入框
                if "+86" in box_hint or "手机" in box_hint or "手机号" in box_hint:
                    continue
                if "密码" in box_hint or "密码" in box_text or "password" in box_hint.lower():
                    self.log(f"[思考] 通过提示文字找到密码输入框: 输入框[{i}]")
                    return box
            
            if len(input_boxes) >= 2:
                # 选择第二个输入框（如果第一个是手机号）
                for i, box in enumerate(input_boxes):
                    hint = self._element_value(box, "hint")
                    if "+86" in hint or "手机" in hint or "手机号" in hint:
                        # 第一个是手机号，第二个应该是密码
                        if i + 1 < len(input_boxes):
                            self.log(f"[思考] 未找到明确的密码输入框，选择输入框[{i+1}]（密码框位置）")
                            return input_boxes[i + 1]
                
                self.log(f"[思考] 未找到明确的密码输入框，选择第二个输入框")
                return input_boxes[1]
        
        # 默认选择第一个输入框
        self.log(f"[思考] 未找到匹配的输入框，选择第一个输入框")
        return input_boxes[0]
    
    def _execute_step(self) -> StepResult:
        """执行单步推理"""
        step = self.state.step_count + 1
        started_at = time.perf_counter()
        task_id = getattr(self.state, "task_id", None)
        self.emit_event("step_start", {"title": f"Step {step}"}, step=step, task_id=task_id)
        self.log(f"[Step {step}] 开始执行")

        # Natural-language inference is only a hint. The LLM must own each
        # ReAct decision so regex parsing cannot become a hidden pseudo-agent.
        messages = self._build_messages()

        response = self.llm_client.chat(
            messages=messages, system_prompt=self._get_system_prompt()
        )

        tool_calls = response.get("tool_calls", [])
        content = response.get("content", "")
        finish_reason = response.get("finish_reason", "stop")

        self.log(f"[Step {step}] LLM 响应类型: {finish_reason}")

        if content:
            # 如果 content 是 dict（来自 _think_before_action），转换为字符串
            if isinstance(content, dict):
                content = content.get("reason", "")
            
            if content:
                # 清洗 LLM content 中的 XML 格式工具调用（如 <arg_key>key</arg_key><arg_value>val</arg_value>）
                # 转为可读的 name(key=value) 格式，仅在展示层处理，不修改原始 content
                display_content = content
                display_lines = []
                for line in display_content.strip().split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    # 检测并清洗 XML 格式的工具调用行
                    if "<arg_key>" in line and "<arg_value>" in line:
                        cleaned = self._clean_xml_tool_call(line)
                        display_lines.append(cleaned if cleaned else line)
                    else:
                        display_lines.append(line)
                
                self.emit_event(
                    "thinking",
                    {"content": content, "finish_reason": finish_reason},
                    step=step,
                    task_id=task_id,
                )
                for line in display_lines:
                    self.log(f"  {line}")

        if finish_reason == "error":
            error_msg = response.get("error", content or "未知错误")
            return self._finish_step_result(
                started_at, step, content, [], [], False, error_msg
            )

        # 当 finish_reason="stop" 且没有结构化 tool_calls 时，
        # 尝试从文本内容中提取 XML 格式的工具调用（LLM 在文本而非 API 中输出工具调用）
        if not tool_calls and content and isinstance(content, str):
            extracted = self._extract_tool_calls_from_text(content)
            if extracted:
                tool_calls = extracted
                finish_reason = "tool_calls"
                self.log(f"[Step {step}] 从文本中提取到 {len(tool_calls)} 个工具调用")

        # 检测重复调用（死循环）
        # 使用滑动窗口检测连续相同的工具调用模式
        if tool_calls:
            # 添加当前调用到历史
            self.state.tool_call_history.append(
                tool_calls.copy() if isinstance(tool_calls, list) else []
            )

            # 只保留最近 N 次调用历史
            history_window = 3
            self.state.tool_call_history = self.state.tool_call_history[
                -history_window:
            ]

            # 检查是否连续多次调用相同的工具
            if len(self.state.tool_call_history) >= history_window:
                # 检查最近几次是否完全相同
                all_same = True
                first_call = self.state.tool_call_history[0]

                for h in self.state.tool_call_history[1:]:
                    if len(h) != len(first_call):
                        all_same = False
                        break
                    for i, tc in enumerate(h):
                        if tc.get("function", {}).get("name") != first_call[i].get(
                            "function", {}
                        ).get("name"):
                            all_same = False
                            break
                        curr_args = tc.get("function", {}).get("arguments", {})
                        prev_args = (
                            first_call[i].get("function", {}).get("arguments", {})
                        )
                        if curr_args != prev_args:
                            all_same = False
                            break
                    if not all_same:
                        break

                if all_same and first_call:
                    tool_name = first_call[0].get("function", {}).get("name", "")

                    # 探索型工具：滑动/滚动翻页，天然需要重复调用
                    exploration_tools = {"swipe", "scroll"}

                    # 幂等工具（如获取信息类），放宽重复限制
                    idempotent_tools = [
                        "get_ui_tree",
                        "get_current_app",
                        "get_screen_size",
                    ]

                    # 获取近期页面变化状态
                    recent_hashes = self.state.page_hash_history[-history_window:]
                    page_is_changing = len(set(recent_hashes)) > 1

                    if tool_name in exploration_tools:
                        if page_is_changing:
                            # 页面在变化，探索有进展 → 重置历史继续
                            self.state.tool_call_history = []
                            self.log(
                                f"[探索] {tool_name} 使页面持续变化，继续探索"
                            )
                        else:
                            # 页面已触底不变，给予更多机会
                            max_exploration = 8
                            if len(self.state.tool_call_history) >= max_exploration:
                                self.log(
                                    f"[结束] 探索工具 {tool_name} 重复 {len(self.state.tool_call_history)} 次但页面无变化"
                                )
                                self.state.finished = True
                                self.state.success = False
                                self.state.message = f"探索工具 {tool_name} 连续调用页面无变化，已到达边界"
                                self.state.step_count = step
                                return self._finish_step_result(
                                    started_at,
                                    step,
                                    content,
                                    tool_calls,
                                    [],
                                    False,
                                    "探索工具死循环",
                                )
                            self.log(
                                f"[警告] {tool_name} 页面无变化 ({len(self.state.tool_call_history)}/{max_exploration})"
                            )
                    elif tool_name in idempotent_tools:
                        # 幂等工具允许更多次重复（5次）
                        if len(self.state.tool_call_history) >= 5:
                            self.log(
                                f"[结束] 检测到幂等工具重复调用 {len(self.state.tool_call_history)} 次，自动终止任务"
                            )
                            self.state.finished = True
                            self.state.success = False
                            self.state.message = f"检测到幂等工具 {tool_name} 重复调用 {len(self.state.tool_call_history)} 次"
                            self.state.step_count = step
                            return self._finish_step_result(
                                started_at,
                                step,
                                content,
                                tool_calls,
                                [],
                                False,
                                "幂等工具重复检测",
                            )
                    else:
                        # 操作型工具（click_element / input_text 等）
                        if not page_is_changing or len(self.state.tool_call_history) >= 5:
                            # 页面无变化 → 真正死循环；或重复太多次
                            self.log(
                                f"[警告] 检测到重复调用 ({len(self.state.tool_call_history)}/{history_window})"
                            )

                            # 尝试回退到关键节点恢复
                            recovered = self._try_recover_from_loop(tool_name, step)

                            if recovered:
                                # 恢复成功，清除死循环历史，继续执行
                                self.state.tool_call_history = []
                                self.log("[恢复] 已从死循环中恢复，继续执行任务")
                                # 返回一个空结果，让下一步重新决策
                                return self._finish_step_result(
                                    started_at, step, content, tool_calls, [], True, None
                                )
                            else:
                                # 无法恢复，终止任务
                                self.log("[结束] 检测到死循环，无法恢复，自动终止任务")
                                self.state.finished = True
                                self.state.success = False
                                self.state.message = f"检测到重复调用同一工具 {tool_name} {len(self.state.tool_call_history)} 次，任务可能陷入死循环"
                                self.state.step_count = step
                                return self._finish_step_result(
                                    started_at, step, content, tool_calls, [], False, "死循环检测"
                                )
                        else:
                            # 操作型工具但页面有变化，记录日志但暂不判定为死循环
                            self.log(
                                f"[重复] {tool_name} 重复调用但页面有变化 ({len(self.state.tool_call_history)}/{history_window})，继续观察"
                            )

        tool_results = []
        executed_tool_names = []

        for tool_call in tool_calls[: self.config.max_tool_calls_per_step]:
            func = tool_call.get("function", {})
            tool_name = func.get("name", "")
            tool_call_id = tool_call.get("id", "")
            executed_tool_names.append(tool_name)
            arguments = func.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
            click_trace_args = {}
            if tool_name == "click_element":
                click_trace_args = self._parse_tool_arguments(tool_call)
                self.log(
                    "点击追踪: click_element"
                    f"({self._format_click_trace_target(click_trace_args)}) 开始"
                )
            self.emit_event(
                "tool_call",
                {"name": tool_name, "arguments": arguments},
                step=step,
                task_id=task_id,
            )

            if tool_name == "finish":
                # 如果任务只是打开应用，且已经确认应用已启动，直接完成任务
                # 不需要强制获取UI树，避免不必要的延迟
                result = {"success": True, "message": ""}
                if func.get("arguments"):
                    try:
                        args = (
                            json.loads(func["arguments"])
                            if isinstance(func["arguments"], str)
                            else func["arguments"]
                        )
                        result.update(args)
                    except (TypeError, json.JSONDecodeError):
                        pass
                self.state.step_count = step
                self._handle_finish(result)
                self.emit_event(
                    "tool_result",
                    {
                        "name": tool_name,
                        "success": True,
                        "summary": self._summarize_tool_result(result),
                        "raw": result,
                    },
                    step=step,
                    task_id=task_id,
                )
                return self._finish_step_result(
                    started_at, step, content, [tool_call], [result], True
                )

            result = self._execute_tool_call(tool_call)
            if tool_name == "click_element":
                self._log_click_trace_result(click_trace_args, result)
            result_success = self._is_success_result(result)
            self.emit_event(
                "tool_result",
                {
                    "name": tool_name,
                    "success": result_success,
                    "summary": self._summarize_tool_result(result),
                    "raw": result,
                },
                step=step,
                task_id=task_id,
            )
            normalized_result = self._normalize_ui_tree(result)
            if isinstance(normalized_result, UITreeResult):
                self.state.current_ui = self._ui_tree_to_state(normalized_result)

            # 调用感知引擎分析执行结果
            perception = self.perception_engine.perceive(
                action=tool_name,
                result=result,
                current_ui=self._normalize_ui_tree(self.state.current_ui),
            )
            
            # 如果页面发生变化（如 launch_app），重置 UI 状态，强制下一步重新获取
            if perception.page_changed:
                self.state.current_ui = None
                self.log(f"[感知] 页面已变化，重置 UI 状态")
            
            self.emit_event(
                "perception",
                {
                    "observations": perception.observations,
                    "anomalies": perception.anomalies,
                    "recoverable": perception.recoverable,
                    "goal_achieved": perception.goal_achieved,
                },
                step=step,
                task_id=task_id,
            )

            # 根据感知结果输出日志
            for obs in perception.observations:
                self.log(f"[感知] {obs}")

            # 滑动成功后自动刷新 UI 树，让 LLM 能看到新内容
            if tool_name == "swipe" and result_success:
                ui_tree_start = time.perf_counter()
                fresh_ui = self._get_normalized_ui_tree(force_refresh=True)
                ui_tree_duration_ms = int((time.perf_counter() - ui_tree_start) * 1000)
                self.state.current_ui = self._ui_tree_to_state(fresh_ui)
                element_count = self._ui_tree_element_count(fresh_ui)
                self.log(f"[性能] 滑动后刷新 UI 树耗时: {ui_tree_duration_ms}ms, 元素数: {element_count}")

            # 记录操作经验（成功或失败）
            if result_success:
                # 重置连续失败计数
                self.state.consecutive_failure_count = 0
                # 记录成功经验
                context = {
                    "perception": {
                        "page_changed": perception.page_changed,
                        "goal_achieved": perception.goal_achieved,
                        "observations": perception.observations[:3],  # 只记录前3条
                    }
                }
                # 如果有操作前状态，也记录下来
                if hasattr(self, '_current_pre_action_state'):
                    context["pre_action_state"] = self._current_pre_action_state
                    delattr(self, '_current_pre_action_state')

                self._record_success_experience(tool_name, arguments, result, context)
                
                # 更新任务里程碑状态
                self._update_milestone_from_action(tool_name, arguments, result, perception)
            else:
                # 增加连续失败计数
                self.state.consecutive_failure_count += 1
                self.log(f"[失败计数] 连续失败 {self.state.consecutive_failure_count}/{MAX_CONSECUTIVE_FAILURES} 次")
                # 记录失败经验
                lesson = None
                if perception.anomalies:
                    lesson = f"失败原因: {', '.join(perception.anomalies[:2])}"

                self._record_failure_experience(
                    tool_name,
                    arguments,
                    result,
                    lesson=lesson,
                    context={"anomalies": perception.anomalies}
                )

            tool_message = self.llm_client.parse_tool_result(
                tool_call_id=tool_call_id, function_name=tool_name, result=result
            )
            tool_results.append(tool_message)

            if isinstance(result, dict):
                self.log(
                    f"[Step {step}] {tool_name} 结果: {result.get('success', False)}"
                )
            else:
                self.log(f"[Step {step}] {tool_name} 结果: {type(result).__name__}")

            if tool_name == "click_element" and not self._is_success_result(result):
                # 点击失败，尝试获取 UI 树进行恢复
                self.log(f"[思考] 点击失败，尝试获取 UI 树进行恢复...")
                ui_tree_start = time.perf_counter()
                ui_tree = self._get_normalized_ui_tree(force_refresh=True)
                ui_tree_duration_ms = int((time.perf_counter() - ui_tree_start) * 1000)
                self.state.current_ui = self._ui_tree_to_state(ui_tree)
                element_count = self._ui_tree_element_count(ui_tree)
                self.log(f"[性能] 点击失败恢复获取 UI 树耗时: {ui_tree_duration_ms}ms, 元素数: {element_count}")
                
                recovery = self._recover_click_from_ui_tree(step, tool_call)
                tool_calls.extend(recovery.get("tool_calls", []))
                tool_results.extend(recovery.get("tool_results", []))
                if recovery.get("success"):
                    result = recovery.get("result", result)
                else:
                    fallback_error = recovery.get(
                        "error", "点击恢复失败，等待下一轮规划"
                    )
                    decision = self._classify_click_recovery_failure(fallback_error)
                    if decision["fatal"]:
                        self.log(f"[降级] {fallback_error}")
                        self.emit_event(
                            "fallback",
                            {"reason": fallback_error, "tool": tool_name},
                            step=step,
                            task_id=task_id,
                        )
                        self.state.finished = True
                        self.state.success = False
                        self.state.message = fallback_error
                        self.state.step_count = step
                        return self._finish_step_result(
                            started_at,
                            step,
                            content,
                            tool_calls,
                            tool_results,
                            False,
                            fallback_error,
                        )

                    self.log(f"[恢复] {fallback_error}，未达到连续失败阈值，交给下一轮规划")
                    tool_results.append(
                        self.llm_client.parse_tool_result(
                            tool_call_id=tool_call_id,
                            function_name=tool_name,
                            result={"success": False, "message": fallback_error},
                        )
                    )
                    continue

            fallback_error = self._record_recovery_signal(tool_name, result)
            if fallback_error:
                self.log(f"[降级] {fallback_error}")
                self.emit_event(
                    "fallback",
                    {"reason": fallback_error, "tool": tool_name},
                    step=step,
                    task_id=task_id,
                )
                self.state.finished = True
                self.state.success = False
                self.state.message = fallback_error
                self.state.step_count = step
                return self._finish_step_result(
                    started_at,
                    step,
                    content,
                    tool_calls,
                    tool_results,
                    False,
                    fallback_error,
                )

        if tool_results:
            self.state.history.append(
                {"step": step, "tool_calls": tool_calls, "tool_results": tool_results}
            )

        self.state.step_count = step
        
        # 构建步骤结果用于实时评估
        step_result_dict = {
            "step": step,
            "tool_calls": tool_calls,
            "tool_results": tool_results,
            "success": True,
            "error": None
        }
        
        # 实时评估和增量学习（非阻塞）
        if self.reactive_reflexor:
            try:
                eval_status = self.reactive_reflexor.quick_evaluate(step_result_dict)
                if eval_status != "success":
                    self.log(f"[反思] 步骤 {step} 评估: {eval_status}")
                
                context = self._get_reflection_context()
                self.reactive_reflexor.incremental_learn(step_result_dict, context)
            except Exception as e:
                agent_logger.debug(f"实时反思评估异常: {e}")

        if (
            not self.state.finished
            and "get_ui_tree" not in executed_tool_names
        ):
            ui_tree_start = time.perf_counter()
            force_refresh = False
            if self.state.current_ui is None:
                force_refresh = True
            
            ui_tree = self._get_normalized_ui_tree(force_refresh=force_refresh)
            ui_tree_duration_ms = int((time.perf_counter() - ui_tree_start) * 1000)
            
            self.state.current_ui = self._ui_tree_to_state(ui_tree)
            element_count = self._ui_tree_element_count(ui_tree)
            
            self.log(f"[性能] Step {step} UI树获取耗时: {ui_tree_duration_ms}ms, 元素数: {element_count}")
            
            fallback_error = self._record_recovery_signal(
                "get_ui_tree", ui_tree
            )
            if fallback_error:
                self.log(f"[降级] {fallback_error}")
                self.emit_event(
                    "fallback",
                    {"reason": fallback_error, "tool": "get_ui_tree"},
                    step=step,
                    task_id=task_id,
                )
                self.state.finished = True
                self.state.success = False
                self.state.message = fallback_error
                return self._finish_step_result(
                    started_at,
                    step,
                    content,
                    tool_calls,
                    tool_results,
                    False,
                    fallback_error,
                )

        # 停滞检测：页面状态连续多次不变
        stagnation_result = self._detect_stagnation(step)
        if stagnation_result:
            self.state.finished = True
            self.state.success = stagnation_result.get("success", False)
            self.state.message = stagnation_result.get("message", "")
            self.state.step_count = step
            return self._finish_step_result(
                started_at,
                step,
                content,
                tool_calls,
                tool_results,
                stagnation_result.get("success", False),
                stagnation_result.get("message", ""),
            )

        return self._finish_step_result(
            started_at, step, content, tool_calls, tool_results, True
        )

    def run(self, task: str, task_id: Optional[int] = None) -> AgentState:
        """
        执行任务

        Args:
            task: 用户任务描述
            task_id: Web UI 任务 ID（可选）

        Returns:
            AgentState: 最终状态
        """
        self.log(f"[启动] 开始执行任务: {task}")

        self.emit_event(
            "task_start",
            {
                "task": task,
                "device_id": getattr(self.mcp_tools, "device_id", None),
                "max_steps": self.config.max_steps,
            },
            task_id=task_id,
        )

        # 任务预分析：检查是否属于Agent能力范围
        analysis_result = TaskAnalyzer.analyze_task(task)
        self.log(f"[任务分析] {analysis_result['reason']}")
        self.emit_event(
            "task_analysis",
            {
                "reason": analysis_result.get("reason", ""),
                "required_app": analysis_result.get("required_app"),
                "task_type": analysis_result.get("task_type", ""),
                "can_handle": analysis_result.get("can_handle", True),
            },
            task_id=task_id,
        )

        if analysis_result.get("is_rejected", False):
            # 任务被拒绝，直接返回失败状态
            self.log(f"[拒绝] 任务超出能力范围: {analysis_result['suggestion']}")
            self.state = AgentState(
                task=task,
                task_id=task_id,
                max_steps=self.config.max_steps,
                finished=True,
                success=False,
                message=f"任务无法执行：{analysis_result['reason']}\n\n{analysis_result['suggestion']}",
            )
            self.emit_event(
                "task_end",
                {
                    "success": self.state.success,
                    "message": self.state.message,
                    "summary": {"total": 0, "passed": 0, "failed": 0},
                },
                task_id=task_id,
            )
            return self.state

        # 如果需要特定应用，记录下来
        if analysis_result.get("required_app"):
            self.log(f"[提示] 任务需要应用: {analysis_result['required_app']}")

        self.state = AgentState(
            task=task, task_id=task_id, max_steps=self.config.max_steps
        )
        
        # 初始化任务状态跟踪器
        self._init_task_tracker(task, analysis_result)
        
        try:
            from backend.utils.script_generator import infer_tool_calls_from_task

            self.state.inferred_tool_calls = infer_tool_calls_from_task(task)
        except Exception:
            self.state.inferred_tool_calls = []

        # 移除启动时不必要的UI树获取 - 应该在执行操作后再获取

        while not self.state.finished and self.state.step_count < self.state.max_steps:
            try:
                step_result = self._execute_step()
                self.state.step_results.append(step_result)

                if step_result.error:
                    self.log(f"[Step {step_result.step}] 错误: {step_result.error}")
                    if self.config.stop_on_error:
                        self.state.finished = True
                        self.state.success = False
                        self.state.message = f"执行错误: {step_result.error}"
                        break

            except Exception as e:
                self.log(f"[执行异常] {e}")
                if self.config.stop_on_error:
                    self.state.finished = True
                    self.state.success = False
                    self.state.message = f"执行异常: {str(e)}"
                    break

        if not self.state.finished:
            self.log(f"[限制] 达到最大步数限制 ({self.state.max_steps})")
            self.state.finished = True
            self.state.success = False
            self.state.message = f"达到最大步数限制 ({self.state.max_steps} 步)"

        self.log(
            f"[结束] 任务{'成功' if self.state.success else '失败'}: {self.state.message}"
        )
        
        # 输出自我进化统计
        if self.evolution_stats["failures_learned"] > 0 or self.evolution_stats["patterns_recognized"] > 0:
            self.log(f"[自我进化] 本次任务进化统计:")
            self.log(f"  - 从失败中学习: {self.evolution_stats['failures_learned']} 次")
            self.log(f"  - 识别成功/失败模式: {self.evolution_stats['patterns_recognized']} 次")
        
        # 输出持久化记忆统计
        if self.persistent_memory:
            try:
                stats = self.persistent_memory.get_statistics()
                self.log(f"[持久化记忆] 总经验数: {stats['total_experiences']}, 成功率: {stats['success_rate']:.1%}")
            except Exception:
                pass
        
        # 批量保存本次任务的所有经验到数据库（非阻塞）
        if self.persistent_memory and self.task_experiences:
            self._persist_task_experiences_async()
        
        # 后台执行自我进化分析（非阻塞）
        if self.persistent_memory and self.evolution_stats["failures_learned"] > 0:
            self._run_evolution_analysis_async()
        
        self.emit_event(
            "task_end",
            {
                "success": self.state.success,
                "message": self.state.message,
                "summary": {
                    "total": len(self.state.step_results),
                    "passed": sum(1 for item in self.state.step_results if item.success),
                    "failed": sum(
                        1 for item in self.state.step_results if not item.success
                    ),
                },
                "evolution_stats": self.evolution_stats,
            },
            task_id=task_id,
        )
        
        # 执行完整反思（非阻塞）
        if self.reflection_loop:
            task_log = self._build_task_log()
            _evolution_executor.submit(self._run_reflection, task_log)

        return self.state
    
    def _run_reflection(self, task_log: Dict[str, Any]):
        """执行完整反思流程（后台线程）"""
        try:
            if not self.reflection_loop:
                return
            
            result = self.reflection_loop.reflect(task_log)
            
            # 输出反思结果
            eval_summary = f"评估等级: {result.evaluation.get('grade', 'N/A')}"
            self.log(f"[反思完成] {eval_summary}")
            
            if not result.evaluation.get("success", False):
                root_cause = result.analysis.get("root_cause", "")
                if root_cause:
                    self.log(f"[反思分析] 失败根因: {root_cause[:100]}...")
            
            if result.distilled.get("strategy"):
                strategy_name = result.distilled["strategy"].get("name", "N/A")
                self.log(f"[知识沉淀] 提炼策略: {strategy_name}")
            
            if result.distilled.get("failure"):
                failure_type = result.distilled["failure"].get("type", "N/A")
                self.log(f"[失败模式] 识别失败模式: {failure_type}")
            
        except Exception as e:
            agent_logger.debug(f"完整反思执行异常: {e}")
    
    def _persist_task_experiences_async(self):
        """
        异步保存任务经验到数据库（后台线程执行，不阻塞主流程）
        """
        experiences = self.task_experiences.copy()
        
        def persist_worker():
            """后台工作线程：批量保存经验"""
            try:
                for exp in experiences:
                    exp_type = exp["type"]
                    key = exp["key"]
                    experience = exp["experience"]
                    
                    self.persistent_memory.save_experience(
                        experience_key=key,
                        tool_name=experience["tool_name"],
                        arguments=experience["arguments"],
                        result=experience["result"],
                        experience_type=exp_type,
                        lesson=experience.get("lesson"),
                        context=experience.get("context"),
                        sync=False  # 使用批量写入
                    )
                
                # 手动刷新缓冲区
                self.persistent_memory.flush()
                agent_logger.debug(f"[持久化记忆] 异步保存了 {len(experiences)} 条经验")
            except Exception as e:
                agent_logger.error(f"[持久化记忆] 异步保存失败: {e}")
        
        # 在后台线程执行
        threading.Thread(
            target=persist_worker,
            name="persist_task_experiences",
            daemon=True
        ).start()
    
    def _run_evolution_analysis_async(self):
        """
        异步执行自我进化分析（后台线程执行，不阻塞主流程）
        """
        failures = [exp for exp in self.task_experiences if exp["type"] == "failure"]
        
        def evolution_worker():
            """后台工作线程：执行自我进化分析"""
            try:
                for failure_exp in failures:
                    experience = failure_exp["experience"]
                    tool_name = experience["tool_name"]
                    arguments = experience["arguments"]
                    lesson = experience.get("lesson")
                    context = experience.get("context")
                    
                    # 分析失败模式
                    failure_pattern = self._analyze_failure_pattern(
                        tool_name, arguments, lesson, context
                    )
                    
                    if failure_pattern:
                        # 检查是否需要记录进化
                        similar_failures = self.persistent_memory.query_similar_experiences(
                            tool_name, limit=5
                        )
                        failure_count = len([
                            f for f in similar_failures 
                            if f.get("experience_type") == "failure"
                        ])
                        
                        if failure_count >= 3:
                            self.persistent_memory.log_evolution(
                                evolution_type="failure_pattern_recognition",
                                description=f"识别到 {tool_name} 的重复失败模式，已记录教训",
                                before_state={
                                    "tool_name": tool_name,
                                    "failure_count": failure_count
                                },
                                after_state={
                                    "lesson": lesson,
                                    "suggested_action": self._suggest_improvement(tool_name, lesson)
                                },
                                impact_score=0.7,
                                sync=False
                            )
                            self.persistent_memory.flush()
                
                agent_logger.debug(f"[自我进化] 异步完成 {len(failures)} 次失败分析")
            except Exception as e:
                agent_logger.error(f"[自我进化] 异步分析失败: {e}")
        
        # 使用线程池执行
        _evolution_executor.submit(evolution_worker)

    async def run_async(self, task: str, task_id: Optional[int] = None) -> AgentState:
        """
        异步执行任务

        Args:
            task: 用户任务描述
            task_id: Web UI 任务 ID（可选）

        Returns:
            AgentState: 最终状态
        """
        return await asyncio.to_thread(self.run, task, task_id)

    def get_step_result(self, step: int) -> Optional[StepResult]:
        """获取指定步骤的结果"""
        for result in self.state.step_results:
            if result.step == step:
                return result
        return None


def create_react_agent(
    llm_base_url: str,
    llm_api_key: str,
    llm_model: str,
    device_id: Optional[str] = None,
    device_type: str = "adb",
    config: Optional[AgentConfig] = None,
    log_callback: Optional[Callable[[str], None]] = None,
) -> ReActAgent:
    """
    工厂函数：创建 ReAct Agent

    Args:
        llm_base_url: LLM API 地址
        llm_api_key: API 密钥
        llm_model: 模型名称
        device_id: 设备 ID
        device_type: 设备类型 (adb/hdc/ios)
        config: Agent 配置
        log_callback: 日志回调

    Returns:
        ReActAgent 实例
    """
    from backend.llm.llm_protocols import OpenAIProtocol
    from backend.mcp.mcp_tools_adb import ADBMCTools

    llm_protocol = OpenAIProtocol(
        base_url=llm_base_url, apikey=llm_api_key, model=llm_model
    )

    if device_type == "adb":
        mcp_tools = ADBMCTools(device_id=device_id)
    else:
        raise ValueError(f"不支持的设备类型: {device_type}")

    llm_client = ReActAgentLLMClient(
        llm_protocol=llm_protocol,
        tools=mcp_tools.get_tool_definitions(),
        log_callback=log_callback,
    )

    return ReActAgent(
        llm_client=llm_client,
        mcp_tools=mcp_tools,
        config=config,
        log_callback=log_callback,
    )
