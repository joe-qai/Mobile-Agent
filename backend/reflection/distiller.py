"""
知识提炼器（Knowledge Distiller）
- 从评估+分析结果中提炼可复用知识
- 成功 → 提炼策略（做什么、怎么做、什么条件下）
- 失败 → 提炼失败模式（什么错、为什么、怎么修、怎么避）
"""

from typing import Optional


class DistilledStrategy:
    """提炼的成功策略"""
    def __init__(self, name: str, task_type: str, steps: list[str],
                 conditions: dict, confidence: float, key_decisions: list[str]):
        self.name = name
        self.task_type = task_type
        self.steps = steps             # 成功的执行路径
        self.conditions = conditions   # 适用条件
        self.confidence = confidence   # 置信度
        self.key_decisions = key_decisions  # 关键的正确决策点

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "task_type": self.task_type,
            "steps": self.steps,
            "conditions": self.conditions,
            "confidence": self.confidence,
            "key_decisions": self.key_decisions,
        }


class DistilledFailure:
    """提炼的失败模式"""
    def __init__(self, name: str, error_type: str, root_cause: str,
                 solution: str, avoidance: str, frequency: int = 1):
        self.name = name
        self.error_type = error_type    # 感知/思考/决策/行动
        self.root_cause = root_cause
        self.solution = solution
        self.avoidance = avoidance
        self.frequency = frequency

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "error_type": self.error_type,
            "root_cause": self.root_cause,
            "solution": self.solution,
            "avoidance": self.avoidance,
            "frequency": self.frequency,
        }


class KnowledgeDistiller:
    """
    知识提炼器：从执行结果中提取可复用知识
    
    用法：
        distiller = KnowledgeDistiller(llm_client)  # 可选LLM
        result = distiller.distill(task_log, evaluation, analysis)
        
        if result.strategy:
            long_term.add_strategy(result.strategy)
        if result.failure:
            long_term.add_failure_pattern(result.failure)
    """

    def __init__(self, llm_client=None):
        self.llm = llm_client

    def distill(self, task_log: dict, evaluation: dict,
                analysis: dict) -> dict:
        """
        从执行+评估+分析中提炼知识
        
        Args:
            task_log: 完整任务日志
            evaluation: TaskEvaluation.to_dict()
            analysis: RootCauseAnalysis.to_dict()
        Returns:
            {
                "strategy": DistilledStrategy或None,   # 成功时提炼
                "failure": DistilledFailure或None,      # 失败时提炼
                "ui_knowledge": dict或None,             # UI结构知识
            }
        """
        result = {"strategy": None, "failure": None, "ui_knowledge": None}

        is_success = evaluation.get("success", False)

        if is_success:
            result["strategy"] = self._distill_strategy(task_log, evaluation)
        else:
            result["failure"] = self._distill_failure(task_log, evaluation, analysis)

        # 提炼UI知识
        result["ui_knowledge"] = self._distill_ui_knowledge(task_log)

        return result

    def _distill_strategy(self, task_log: dict,
                          evaluation: dict) -> Optional[DistilledStrategy]:
        """从成功任务中提炼策略"""
        goal = task_log.get("goal", "")
        steps = task_log.get("steps", [])
        sub_tasks = task_log.get("sub_tasks", [])
        environment = task_log.get("environment", {})

        if self.llm:
            return self._distill_strategy_with_llm(task_log, evaluation)
        else:
            return self._distill_strategy_with_rules(task_log, evaluation)

    def _distill_strategy_with_rules(self, task_log: dict,
                                     evaluation: dict) -> DistilledStrategy:
        """规则based策略提炼"""
        goal = task_log.get("goal", "")
        sub_tasks = task_log.get("sub_tasks", [])
        steps = task_log.get("steps", [])
        environment = task_log.get("environment", {})

        # 成功路径 = 成功步骤的描述序列
        success_path = [s.get("description", "") for s in steps if s.get("success")]

        # 关键决策 = 重试0次且成功的步骤（说明一次做对了）
        key_decisions = [
            s.get("description", "")
            for s in steps
            if s.get("success") and s.get("retry_count", 0) == 0
        ]

        # 从目标中提取任务类型关键词
        task_type = self._extract_task_type(goal)

        # 置信度基于效率
        confidence = evaluation.get("efficiency", 0.5) * 0.8 + 0.2  # 最低0.2

        return DistilledStrategy(
            name=f"策略: {task_type}",
            task_type=task_type,
            steps=success_path,
            conditions=environment,
            confidence=confidence,
            key_decisions=key_decisions,
        )

    def _distill_strategy_with_llm(self, task_log: dict,
                                   evaluation: dict) -> DistilledStrategy:
        """LLMbased策略提炼（更精细）"""
        prompt = f"""从以下成功任务执行中提炼可复用策略：

任务目标: {task_log.get('goal', '')}
执行步骤: {[s.get('description') for s in task_log.get('steps', [])]}
效率: {evaluation.get('efficiency', 0)}
重试率: {evaluation.get('retry_rate', 0)}
环境: {task_log.get('environment', {})}

请输出JSON格式：
```json
{
  "name": "策略名称",
  "task_type": "任务类型关键词",
  "steps": ["步骤1", "步骤2", ...],
  "conditions": {"适用条件key": "value"},
  "confidence": 0.0~1.0,
  "key_decisions": ["关键决策1", "关键决策2"]
}
```"""
        response = self.llm.chat(prompt)
        # 解析...（类似analyzer的解析逻辑）
        import json
        import re
        match = re.search(r'\{[^{}]+\}', response, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                return DistilledStrategy(**data)
            except:
                pass
        # 降级到规则模式
        return self._distill_strategy_with_rules(task_log, evaluation)

    def _distill_failure(self, task_log: dict, evaluation: dict,
                         analysis: dict) -> DistilledFailure:
        """从失败任务中提炼失败模式"""
        goal = task_log.get("goal", "")
        task_type = self._extract_task_type(goal)

        return DistilledFailure(
            name=f"失败模式: {task_type} - {analysis.get('failure_layer', '未知')}",
            error_type=analysis.get("failure_layer", "未知"),
            root_cause=analysis.get("root_cause", ""),
            solution=analysis.get("solution", ""),
            avoidance=analysis.get("avoidance", ""),
        )

    def _distill_ui_knowledge(self, task_log: dict) -> Optional[dict]:
        """从任务中提取UI结构知识"""
        device_state = task_log.get("device_state_final", {})
        if not device_state:
            return None

        # 如果有screen信息，提取为UI知识
        screen = device_state.get("screen", device_state.get("page", ""))
        if not screen:
            return None

        elements = device_state.get("elements", [])
        if not elements:
            return None

        return {screen: elements}

    def _extract_task_type(self, goal: str) -> str:
        """从目标描述中提取任务类型关键词"""
        # 常见任务类型关键词映射
        type_keywords = {
            "登录": "登录测试",
            "注册": "注册流程",
            "设置": "设置修改",
            "配网": "设备配网",
            "开锁": "开锁操作",
            "绑定": "设备绑定",
            "添加": "添加设备",
            "删除": "删除操作",
            "升级": "固件升级",
            "测试": "功能测试",
        }

        for kw, task_type in type_keywords.items():
            if kw in goal:
                return task_type

        # 未匹配 → 取目标的前10字
        return goal[:10]