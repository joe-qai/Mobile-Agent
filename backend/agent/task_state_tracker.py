"""
任务状态跟踪器（Task State Tracker）

用于跟踪任务的执行进度，维护关键里程碑状态，帮助 Agent 做出更好的决策。

核心功能：
1. 任务分解：将复杂任务分解为子任务/里程碑
2. 状态跟踪：记录每个里程碑的完成状态
3. 关键节点：标记可回退的关键节点
4. 上下文传递：跨步骤传递关键信息
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class MilestoneStatus(Enum):
    """里程碑状态"""
    PENDING = "pending"      # 待执行
    IN_PROGRESS = "in_progress"  # 执行中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"        # 失败
    SKIPPED = "skipped"      # 跳过


@dataclass
class Milestone:
    """里程碑/子任务"""
    id: str
    name: str
    description: str = ""
    status: MilestoneStatus = MilestoneStatus.PENDING
    completed_at: Optional[float] = None
    context: Dict[str, Any] = field(default_factory=dict)  # 里程碑相关的上下文信息
    dependencies: List[str] = field(default_factory=list)  # 依赖的其他里程碑
    
    def mark_completed(self, context: Dict[str, Any] = None):
        """标记里程碑完成"""
        self.status = MilestoneStatus.COMPLETED
        self.completed_at = time.time()
        if context:
            self.context.update(context)
    
    def mark_failed(self, reason: str = ""):
        """标记里程碑失败"""
        self.status = MilestoneStatus.FAILED
        self.context["failure_reason"] = reason
    
    def is_ready(self, completed_milestones: set) -> bool:
        """检查里程碑是否可以执行（依赖已满足）"""
        return all(dep in completed_milestones for dep in self.dependencies)


@dataclass
class KeyNode:
    """关键节点 - 可回退的状态快照"""
    step: int
    name: str
    description: str = ""
    ui_state_hash: str = ""  # UI 状态哈希（用于判断是否回退到这个状态）
    created_at: float = field(default_factory=time.time)
    context: Dict[str, Any] = field(default_factory=dict)


class TaskStateTracker:
    """
    任务状态跟踪器
    
    用于跟踪任务的执行进度，维护关键里程碑状态。
    
    使用示例：
    ```python
    tracker = TaskStateTracker()
    
    # 添加里程碑
    tracker.add_milestone("launch_app", "启动应用")
    tracker.add_milestone("input_password", "输入密码", dependencies=["launch_app"])
    tracker.add_milestone("click_login", "点击登录", dependencies=["input_password"])
    
    # 标记里程碑完成
    tracker.complete_milestone("launch_app", {"app": "鹿客管家"})
    tracker.complete_milestone("input_password", {"password_length": 9})
    
    # 检查进度
    print(tracker.get_progress_summary())
    ```
    """
    
    # 常见的任务模板
    LOGIN_TASK_TEMPLATE = [
        Milestone("launch_app", "启动应用"),
        Milestone("navigate_to_login", "导航到登录页面"),
        Milestone("switch_to_password", "切换到密码登录"),
        Milestone("input_password", "输入密码"),
        Milestone("agree_terms", "同意用户协议"),
        Milestone("click_login", "点击登录按钮"),
        Milestone("verify_login", "验证登录结果"),
    ]
    
    def __init__(self):
        self.milestones: Dict[str, Milestone] = {}
        self.key_nodes: List[KeyNode] = []
        self.current_step: int = 0
        self.task_context: Dict[str, Any] = {}  # 任务级别的上下文
        self._milestone_order: List[str] = []  # 里程碑执行顺序
    
    def add_milestone(
        self, 
        id: str, 
        name: str, 
        description: str = "",
        dependencies: List[str] = None
    ):
        """添加里程碑"""
        milestone = Milestone(
            id=id,
            name=name,
            description=description,
            dependencies=dependencies or []
        )
        self.milestones[id] = milestone
        self._milestone_order.append(id)
    
    def use_template(self, template_name: str):
        """使用预定义的任务模板"""
        if template_name == "login":
            for milestone in self.LOGIN_TASK_TEMPLATE:
                self.milestones[milestone.id] = Milestone(
                    id=milestone.id,
                    name=milestone.name,
                    description=milestone.description,
                    dependencies=milestone.dependencies.copy()
                )
                self._milestone_order.append(milestone.id)
    
    def start_milestone(self, id: str) -> bool:
        """开始执行里程碑"""
        if id not in self.milestones:
            return False
        
        milestone = self.milestones[id]
        
        # 检查依赖
        completed = self.get_completed_milestone_ids()
        if not milestone.is_ready(completed):
            return False
        
        milestone.status = MilestoneStatus.IN_PROGRESS
        return True
    
    def complete_milestone(self, id: str, context: Dict[str, Any] = None) -> bool:
        """完成里程碑"""
        if id not in self.milestones:
            return False
        
        self.milestones[id].mark_completed(context)
        
        # 自动创建关键节点
        self._auto_create_key_node(id)
        
        return True
    
    def fail_milestone(self, id: str, reason: str = "") -> bool:
        """标记里程碑失败"""
        if id not in self.milestones:
            return False
        
        self.milestones[id].mark_failed(reason)
        return True
    
    def get_completed_milestone_ids(self) -> set:
        """获取已完成的里程碑 ID 集合"""
        return {
            id for id, m in self.milestones.items() 
            if m.status == MilestoneStatus.COMPLETED
        }
    
    def get_current_milestone(self) -> Optional[Milestone]:
        """获取当前应该执行的里程碑"""
        completed = self.get_completed_milestone_ids()
        
        for id in self._milestone_order:
            milestone = self.milestones[id]
            if milestone.status == MilestoneStatus.PENDING:
                if milestone.is_ready(completed):
                    return milestone
            elif milestone.status == MilestoneStatus.IN_PROGRESS:
                return milestone
        
        return None
    
    def get_next_milestone(self) -> Optional[Milestone]:
        """获取下一个里程碑"""
        current = self.get_current_milestone()
        if not current:
            return None
        
        # 找到当前里程碑在顺序中的位置
        try:
            current_idx = self._milestone_order.index(current.id)
            for id in self._milestone_order[current_idx + 1:]:
                milestone = self.milestones[id]
                if milestone.status == MilestoneStatus.PENDING:
                    return milestone
        except ValueError:
            pass
        
        return None
    
    def is_milestone_completed(self, id: str) -> bool:
        """检查里程碑是否已完成"""
        if id not in self.milestones:
            return False
        return self.milestones[id].status == MilestoneStatus.COMPLETED
    
    def set_context(self, key: str, value: Any):
        """设置任务上下文"""
        self.task_context[key] = value
    
    def get_context(self, key: str, default: Any = None) -> Any:
        """获取任务上下文"""
        return self.task_context.get(key, default)
    
    def save_key_node(self, step: int, name: str, description: str = "", context: Dict[str, Any] = None):
        """保存关键节点"""
        node = KeyNode(
            step=step,
            name=name,
            description=description,
            context=context or {}
        )
        self.key_nodes.append(node)
    
    def get_last_key_node(self) -> Optional[KeyNode]:
        """获取最后一个关键节点"""
        if self.key_nodes:
            return self.key_nodes[-1]
        return None
    
    def get_progress_summary(self) -> Dict[str, Any]:
        """获取进度摘要"""
        total = len(self.milestones)
        completed = len(self.get_completed_milestone_ids())
        failed = len([m for m in self.milestones.values() if m.status == MilestoneStatus.FAILED])
        
        return {
            "total_milestones": total,
            "completed": completed,
            "failed": failed,
            "progress": f"{completed}/{total}",
            "progress_percent": (completed / total * 100) if total > 0 else 0,
            "current_milestone": self.get_current_milestone().name if self.get_current_milestone() else None,
            "key_nodes_count": len(self.key_nodes),
        }
    
    def get_milestone_context_summary(self) -> str:
        """获取里程碑上下文摘要（用于 LLM 提示）"""
        lines = ["任务进度:"]
        
        for id in self._milestone_order:
            milestone = self.milestones[id]
            status_icon = {
                MilestoneStatus.PENDING: "⏳",
                MilestoneStatus.IN_PROGRESS: "🔄",
                MilestoneStatus.COMPLETED: "✅",
                MilestoneStatus.FAILED: "❌",
                MilestoneStatus.SKIPPED: "⏭️",
            }.get(milestone.status, "❓")
            
            lines.append(f"  {status_icon} {milestone.name}")
            
            # 如果有上下文信息，也显示
            if milestone.context:
                for key, value in milestone.context.items():
                    if key not in ["failure_reason"]:
                        lines.append(f"      - {key}: {value}")
        
        return "\n".join(lines)
    
    def _auto_create_key_node(self, milestone_id: str):
        """自动创建关键节点"""
        milestone = self.milestones[milestone_id]
        
        # 关键里程碑自动创建节点
        key_milestones = ["launch_app", "input_password", "click_login", "verify_login"]
        
        if milestone_id in key_milestones:
            self.save_key_node(
                step=self.current_step,
                name=f"完成: {milestone.name}",
                description=milestone.description,
                context=milestone.context
            )
    
    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "milestones": {
                id: {
                    "name": m.name,
                    "status": m.status.value,
                    "context": m.context,
                }
                for id, m in self.milestones.items()
            },
            "key_nodes": [
                {
                    "step": n.step,
                    "name": n.name,
                    "context": n.context,
                }
                for n in self.key_nodes
            ],
            "task_context": self.task_context,
            "progress": self.get_progress_summary(),
        }
