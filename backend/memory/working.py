"""
工作记忆（Working Memory）
- 生命周期：单次任务执行期间
- 作用：维护当前任务的上下文、进度和中间状态
- 存储：内存中的结构化字典
- 特点：任务完成后被反思模块消费，然后清空
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class StepRecord:
    """单步执行记录"""
    step_id: str
    description: str
    action: str               # 执行的动作
    observation: str          # 观察到的结果
    success: bool             # 是否成功
    retry_count: int = 0      # 重试次数
    duration_ms: float = 0    # 耗时(ms)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "description": self.description,
            "action": self.action,
            "observation": self.observation,
            "success": self.success,
            "retry_count": self.retry_count,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
        }


@dataclass
class TaskContext:
    """当前任务上下文"""
    task_id: str
    goal: str                  # 任务目标
    sub_tasks: list[str]       # 子任务列表
    current_sub_task_idx: int = 0  # 当前执行的子任务索引
    device_state: dict = field(default_factory=dict)  # 设备/UI状态
    environment: dict = field(default_factory=dict)    # 环境信息


class WorkingMemory:
    """
    工作记忆：单次任务的执行上下文
    
    用法：
        wm = WorkingMemory()
        wm.start_task("test_login", "测试登录功能", ["打开APP", "输入账号", "点击登录"])
        wm.add_step(StepRecord(...))
        wm.update_device_state({"screen": "login_page"})
        wm.get_context_prompt()  →  给LLM的上下文
        wm.end_task()  →  返回完整任务日志供反思模块消费
    """

    def __init__(self):
        self.context: Optional[TaskContext] = None
        self.steps: list[StepRecord] = []
        self._start_time: float = 0
        self._extra_state: dict = {}  # 额外状态（自定义扩展）

    # ── 任务生命周期 ──

    def start_task(self, task_id: str, goal: str, sub_tasks: list[str],
                   environment: dict = None):
        """开始一个新任务"""
        self.context = TaskContext(
            task_id=task_id,
            goal=goal,
            sub_tasks=sub_tasks,
            environment=environment or {},
        )
        self.steps = []
        self._start_time = time.time()
        self._extra_state = {}

    def end_task(self) -> dict:
        """
        结束任务，返回完整日志供反思模块消费
        
        Returns:
            任务日志字典，包含目标、步骤、结果、统计
        """
        if not self.context:
            return {}

        total_duration = time.time() - self._start_time
        success_count = sum(1 for s in self.steps if s.success)
        total_steps = len(self.steps)
        total_retries = sum(s.retry_count for s in self.steps)

        log = {
            "task_id": self.context.task_id,
            "goal": self.context.goal,
            "sub_tasks": self.context.sub_tasks,
            "environment": self.context.environment,
            "steps": [s.to_dict() for s in self.steps],
            "summary": {
                "total_steps": total_steps,
                "success_count": success_count,
                "success_rate": success_count / max(total_steps, 1),
                "total_duration_ms": total_duration * 1000,
                "total_retries": total_retries,
                "is_completed": self.context.current_sub_task_idx >= len(self.context.sub_tasks),
            },
            "device_state_final": self.context.device_state,
        }

        # 清空工作记忆
        self.context = None
        self.steps = []
        self._extra_state = {}

        return log

    # ── 步骤管理 ──

    def add_step(self, step: StepRecord):
        """添加一步执行记录"""
        self.steps.append(step)
        if step.success:
            self.context.current_sub_task_idx += 1

    def get_last_step(self) -> Optional[StepRecord]:
        """获取最近一步"""
        return self.steps[-1] if self.steps else None

    def get_failed_steps(self) -> list[StepRecord]:
        """获取所有失败的步骤"""
        return [s for s in self.steps if not s.success]

    def get_retry_summary(self) -> dict[str, int]:
        """各步骤的重试次数"""
        return {s.step_id: s.retry_count for s in self.steps if s.retry_count > 0}

    # ── 状态更新 ──

    def update_device_state(self, state: dict):
        """更新设备/UI感知状态"""
        if self.context:
            self.context.device_state.update(state)

    def update_environment(self, env: dict):
        """更新环境信息"""
        if self.context:
            self.context.environment.update(env)

    def set_extra_state(self, key: str, value: Any):
        """设置自定义扩展状态"""
        self._extra_state[key] = value

    def get_extra_state(self, key: str, default=None) -> Any:
        """获取自定义扩展状态"""
        return self._extra_state.get(key, default)

    # ── 给LLM的上下文 ──

    def get_context_prompt(self) -> str:
        """
        组装给LLM的上下文prompt
        
        包含：目标 + 已完成步骤 + 当前状态 + 下一步指引
        """
        if not self.context:
            return ""

        parts = []
        parts.append(f"## 当前任务\n目标: {self.context.goal}")
        parts.append(f"子任务列表: {self.context.sub_tasks}")

        # 已完成的步骤
        if self.steps:
            parts.append("\n## 已执行步骤")
            for s in self.steps[-5:]:  # 最近5步，避免上下文过长
                status = "✅" if s.success else "❌"
                parts.append(f"{status} [{s.step_id}] {s.description} → {s.observation}")

        # 失败步骤提醒
        failed = self.get_failed_steps()
        if failed:
            parts.append("\n## ⚠️ 失败步骤（需要重点关注）")
            for s in failed:
                parts.append(f"❌ [{s.step_id}] {s.description} — 原因: {s.observation} (重试{s.retry_count}次)")

        # 当前设备状态
        if self.context.device_state:
            parts.append("\n## 当前设备状态")
            parts.append(json.dumps(self.context.device_state, ensure_ascii=False, indent=2))

        # 下一步指引
        remaining = self.context.sub_tasks[self.context.current_sub_task_idx:]
        if remaining:
            parts.append(f"\n## 下一步\n待完成子任务: {remaining}")
            parts.append(f"当前应执行: {remaining[0]}")

        # 自定义状态
        if self._extra_state:
            parts.append("\n## 扩展状态")
            parts.append(json.dumps(self._extra_state, ensure_ascii=False, indent=2))

        return "\n".join(parts)

    def is_task_complete(self) -> bool:
        """任务是否已完成"""
        if not self.context:
            return True
        return self.context.current_sub_task_idx >= len(self.context.sub_tasks)

    def get_progress(self) -> float:
        """任务进度 0.0~1.0"""
        if not self.context or not self.context.sub_tasks:
            return 1.0
        return self.context.current_sub_task_idx / len(self.context.sub_tasks)