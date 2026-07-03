"""
长期记忆（Long-term Memory）
- 生命周期：永久，持续积累
- 作用：领域知识、稳定策略、常见模式
- 存储：JSON文件（初期），后续可升级向量数据库
- 核心：策略库 + 错误模式库 + UI结构库 + 设备知识库
"""

import json
import os
import time
from pathlib import Path
from typing import Optional


class Strategy:
    """成功策略记录"""
    def __init__(self, name: str, task_type: str, steps: list[str],
                 conditions: dict, confidence: float = 1.0,
                 learned_at: float = None, usage_count: int = 0):
        self.name = name
        self.task_type = task_type      # 任务类型（如"登录测试"、"设置修改"）
        self.steps = steps              # 成功的执行路径
        self.conditions = conditions    # 适用条件（设备/APP版本/场景）
        self.confidence = confidence    # 置信度 0~1
        self.learned_at = learned_at or time.time()
        self.usage_count = usage_count

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "task_type": self.task_type,
            "steps": self.steps,
            "conditions": self.conditions,
            "confidence": self.confidence,
            "learned_at": self.learned_at,
            "usage_count": self.usage_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Strategy":
        return cls(**d)


class FailurePattern:
    """失败模式记录"""
    def __init__(self, name: str, error_type: str, root_cause: str,
                 solution: str, avoidance: str, frequency: int = 1,
                 learned_at: float = None):
        self.name = name
        self.error_type = error_type    # 感知/思考/决策/行动
        self.root_cause = root_cause    # 根因
        self.solution = solution        # 解决方案
        self.avoidance = avoidance      # 如何避免
        self.frequency = frequency      # 出现频率
        self.learned_at = learned_at or time.time()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "error_type": self.error_type,
            "root_cause": self.root_cause,
            "solution": self.solution,
            "avoidance": self.avoidance,
            "frequency": self.frequency,
            "learned_at": self.learned_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FailurePattern":
        return cls(**d)


class LongTermMemory:
    """
    长期记忆：持久知识积累
    
    四大知识库：
    1. strategies    — 成功策略库
    2. failure_lib   — 失败模式库
    3. ui_structure  — 稳定UI结构库
    4. device_specs  — 设备特征知识库
    
    用法：
        ltm = LongTermMemory("data/long_term/")
        ltm.add_strategy(Strategy(...))
        ltm.add_failure_pattern(FailurePattern(...))
        ltm.query_strategies("登录测试")
        ltm.query_failures("按钮点击")
        ltm.learn_from_reflection(reflection_result)
    """

    def __init__(self, path: str = "data/long_term/"):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

        self._strategies: list[Strategy] = []
        self._failure_lib: list[FailurePattern] = []
        self._ui_structure: dict = {}
        self._device_specs: dict = {}

        self._load_all()

    def _load_all(self):
        """加载所有知识库"""
        self._strategies = self._load_json_list(
            self.path / "strategies.json", Strategy.from_dict
        )
        self._failure_lib = self._load_json_list(
            self.path / "failure_lib.json", FailurePattern.from_dict
        )
        self._ui_structure = self._load_json_dict(
            self.path / "ui_structure.json"
        )
        self._device_specs = self._load_json_dict(
            self.path / "device_specs.json"
        )

    def _save_all(self):
        """保存所有知识库"""
        self._save_json_list(self.path / "strategies.json", self._strategies)
        self._save_json_list(self.path / "failure_lib.json", self._failure_lib)
        self._save_json_dict(self.path / "ui_structure.json", self._ui_structure)
        self._save_json_dict(self.path / "device_specs.json", self._device_specs)

    @staticmethod
    def _load_json_list(path: Path, constructor) -> list:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [constructor(d) for d in data]
        return []

    @staticmethod
    def _save_json_list(path: Path, items: list):
        with open(path, "w", encoding="utf-8") as f:
            json.dump([item.to_dict() for item in items], f, ensure_ascii=False, indent=2)

    @staticmethod
    def _load_json_dict(path: Path) -> dict:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    @staticmethod
    def _save_json_dict(path: Path, data: dict):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── 策略库 ──

    def add_strategy(self, strategy: Strategy):
        """添加成功策略"""
        # 检查是否已有同类策略，合并而非重复
        existing = self._find_similar_strategy(strategy.task_type, strategy.conditions)
        if existing:
            # 更新置信度和使用次数
            existing.confidence = min(1.0, existing.confidence + 0.1)
            existing.usage_count += 1
            # 如果新路径更短，替换
            if len(strategy.steps) < len(existing.steps):
                existing.steps = strategy.steps
        else:
            self._strategies.append(strategy)
        self._save_all()

    def query_strategies(self, task_type: str,
                         conditions: dict = None) -> list[Strategy]:
        """
        查询相关成功策略
        
        Args:
            task_type: 任务类型关键词
            conditions: 环境条件过滤
        Returns:
            匹配的策略列表，按置信度排序
        """
        matched = []
        for s in self._strategies:
            if task_type.lower() in s.task_type.lower():
                if conditions:
                    # 检查条件是否匹配
                    match = all(
                        s.conditions.get(k) == v
                        for k, v in conditions.items()
                        if k in s.conditions
                    )
                    if not match:
                        continue
                matched.append(s)

        # 按置信度降序
        matched.sort(key=lambda s: s.confidence, reverse=True)
        return matched

    def _find_similar_strategy(self, task_type: str,
                               conditions: dict) -> Optional[Strategy]:
        """查找已有同类策略"""
        for s in self._strategies:
            if s.task_type == task_type:
                if not conditions or all(
                    s.conditions.get(k) == v
                    for k, v in conditions.items()
                    if k in s.conditions
                ):
                    return s
        return None

    # ── 失败模式库 ──

    def add_failure_pattern(self, pattern: FailurePattern):
        """添加失败模式"""
        existing = self._find_similar_failure(pattern.error_type, pattern.root_cause)
        if existing:
            existing.frequency += 1
            # 更新解决方案（如果新的更好）
            if pattern.solution and len(pattern.solution) > len(existing.solution):
                existing.solution = pattern.solution
        else:
            self._failure_lib.append(pattern)
        self._save_all()

    def query_failures(self, task_type: str = None,
                       error_type: str = None) -> list[FailurePattern]:
        """
        查询相关失败模式
        
        Args:
            task_type: 任务类型（模糊匹配name）
            error_type: 错误层级（感知/思考/决策/行动）
        Returns:
            匹配的失败模式列表，按频率降序
        """
        matched = []
        for f in self._failure_lib:
            if task_type and task_type.lower() not in f.name.lower():
                continue
            if error_type and f.error_type != error_type:
                continue
            matched.append(f)

        matched.sort(key=lambda f: f.frequency, reverse=True)
        return matched

    def _find_similar_failure(self, error_type: str,
                              root_cause: str) -> Optional[FailurePattern]:
        """查找已有类似失败模式"""
        for f in self._failure_lib:
            if f.error_type == error_type and f.root_cause == root_cause:
                return f
        return None

    # ── UI结构库 ──

    def update_ui_structure(self, screen_name: str, elements: list[dict]):
        """
        更新UI结构知识
        
        Args:
            screen_name: 页面名称
            elements: 元素列表 [{id, type, text, bounds, stable}]
        """
        if screen_name not in self._ui_structure:
            self._ui_structure[screen_name] = {"elements": elements, "updated_at": time.time()}
        else:
            # 合合更新：稳定元素保留，新增元素加入
            existing_ids = {e["id"] for e in self._ui_structure[screen_name]["elements"]}
            for elem in elements:
                if elem["id"] in existing_ids:
                    # 更新已知元素
                    for i, ex in enumerate(self._ui_structure[screen_name]["elements"]):
                        if ex["id"] == elem["id"]:
                            self._ui_structure[screen_name]["elements"][i] = elem
                            break
                else:
                    self._ui_structure[screen_name]["elements"].append(elem)
            self._ui_structure[screen_name]["updated_at"] = time.time()
        self._save_all()

    def query_ui_structure(self, screen_name: str) -> dict:
        """查询某页面的UI结构"""
        return self._ui_structure.get(screen_name, {})

    def get_stable_elements(self, screen_name: str) -> list[dict]:
        """获取某页面的稳定元素（stability > 0.8）"""
        screen = self._ui_structure.get(screen_name, {})
        return [e for e in screen.get("elements", []) if e.get("stable", False)]

    # ── 设备知识库 ──

    def update_device_spec(self, device_model: str, spec: dict):
        """更新设备特征知识"""
        self._device_specs[device_model] = {
            **spec,
            "updated_at": time.time()
        }
        self._save_all()

    def query_device_spec(self, device_model: str) -> dict:
        """查询设备特征"""
        return self._device_specs.get(device_model, {})

    # ── 从反思结果学习 ──

    def learn_from_reflection(self, reflection_result: dict):
        """
        从反思模块的输出中提取知识并存储
        
        Args:
            reflection_result: ReflectionLoop.reflect() 的返回值
        """
        # 学习成功策略
        if reflection_result.get("success"):
            strategy_data = reflection_result.get("strategy", {})
            if strategy_data:
                self.add_strategy(Strategy(
                    name=strategy_data.get("name", ""),
                    task_type=strategy_data.get("task_type", ""),
                    steps=strategy_data.get("steps", []),
                    conditions=strategy_data.get("conditions", {}),
                    confidence=strategy_data.get("confidence", 0.7),
                ))

        # 学习失败模式
        failure_data = reflection_result.get("failure_analysis", {})
        if failure_data:
            self.add_failure_pattern(FailurePattern(
                name=failure_data.get("name", ""),
                error_type=failure_data.get("error_type", ""),
                root_cause=failure_data.get("root_cause", ""),
                solution=failure_data.get("solution", ""),
                avoidance=failure_data.get("avoidance", ""),
            ))

        # 更新UI结构
        ui_data = reflection_result.get("ui_knowledge", {})
        if ui_data:
            for screen_name, elements in ui_data.items():
                self.update_ui_structure(screen_name, elements)

    # ── 给LLM的知识上下文 ──

    def get_relevant_knowledge_prompt(self, task_goal: str,
                                      device_model: str = None) -> str:
        """
        组装给LLM的相关知识prompt
        
        Args:
            task_goal: 当前任务目标
            device_model: 当前设备型号
        Returns:
            知识上下文文本
        """
        parts = []

        # 相关成功策略
        strategies = self.query_strategies(task_goal)
        if strategies:
            parts.append("## 📚 已知成功策略")
            for s in strategies[:3]:  # 最多3条
                parts.append(f"- **{s.name}** (置信度{s.confidence:.0%}): {s.steps}")
                if s.conditions:
                    parts.append(f"  适用条件: {s.conditions}")

        # 可能遇到的失败模式
        failures = self.query_failures(task_type=task_goal)
        if failures:
            parts.append("\n## ⚠️ 可能遇到的已知问题")
            for f in failures[:3]:
                parts.append(f"- **{f.name}** [{f.error_type}] 根因: {f.root_cause}")
                parts.append(f"  解决: {f.solution} | 避免: {f.avoidance}")

        # 设备特征
        if device_model and device_model in self._device_specs:
            spec = self._device_specs[device_model]
            parts.append(f"\n## 📱 设备知识 ({device_model})")
            parts.append(json.dumps(spec, ensure_ascii=False, indent=2))

        return "\n".join(parts) if parts else ""