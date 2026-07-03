"""
根因分析器（Root Cause Analyzer）
- 使用LLM深度分析失败根因
- 定位失败发生在哪个层级（感知/思考/决策/行动）
- 提出解决方案
"""

from typing import Optional

# 失败层级定义
FAILURE_LAYERS = {
    "感知": "Agent未能正确感知环境/UI状态，如：看不到元素、误读状态、漏掉信息",
    "思考": "Agent推理出错，如：逻辑推断错误、遗漏条件、误解意图",
    "决策": "Agent选择了错误的行动方案，如：选错路径、优先级判断失误",
    "行动": "Agent执行操作失败，如：点击位置偏差、操作时序错误、权限不足",
}


class RootCauseAnalysis:
    """根因分析结果"""
    def __init__(self, failure_layer: str, root_cause: str,
                 evidence: str, solution: str, avoidance: str,
                 confidence: float = 0.7):
        self.failure_layer = failure_layer   # 感知/思考/决策/行动
        self.root_cause = root_cause         # 根因描述
        self.evidence = evidence             # 支撑证据
        self.solution = solution             # 解决方案
        self.avoidance = avoidance           # 如何避免
        self.confidence = confidence         # 分析置信度

    def to_dict(self) -> dict:
        return {
            "failure_layer": self.failure_layer,
            "root_cause": self.root_cause,
            "evidence": self.evidence,
            "solution": self.solution,
            "avoidance": self.avoidance,
            "confidence": self.confidence,
        }


class RootCauseAnalyzer:
    """
    根因分析器：用LLM深度分析失败原因
    
    用法：
        analyzer = RootCauseAnalyzer(llm_client)
        analysis = analyzer.analyze(task_log, evaluation)
        print(analysis.failure_layer)  # "感知"/"思考"/"决策"/"行动"
        print(analysis.root_cause)
        print(analysis.solution)
    """

    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: LLM客户端对象，需支持 .chat(prompt) -> str
                        如果不提供，使用规则-based分析（降级模式）
        """
        self.llm = llm_client

    def analyze(self, task_log: dict, evaluation: dict = None,
                similar_failures: list = None) -> RootCauseAnalysis:
        """
        分析任务失败根因
        
        Args:
            task_log: 完整任务日志
            evaluation: TaskEvaluator.evaluate() 的结果
            similar_failures: 短期记忆中查到的类似失败案例
        Returns:
            RootCauseAnalysis 根因分析结果
        """
        summary = task_log.get("summary", {})
        steps = task_log.get("steps", [])

        # 找到第一个失败步骤
        failed_steps = [s for s in steps if not s.get("success", True)]
        if not failed_steps and not evaluation.get("success", True):
            # 所有步骤都"成功"但任务没完成 → 子任务未全覆盖
            return self._analyze_incomplete(task_log, evaluation)

        if not failed_steps:
            return RootCauseAnalysis(
                failure_layer="未知",
                root_cause="任务标记为失败但无失败步骤",
                evidence="",
                solution="检查任务完成判定逻辑",
                avoidance="确保完成判定与步骤结果一致",
                confidence=0.3,
            )

        # 获取关键失败步骤（第一个失败 + 最严重失败）
        first_failure = failed_steps[0]
        worst_failure = max(failed_steps, key=lambda s: s.get("retry_count", 0))

        if self.llm:
            return self._analyze_with_llm(
                task_log, first_failure, worst_failure,
                evaluation, similar_failures
            )
        else:
            return self._analyze_with_rules(
                task_log, first_failure, worst_failure, similar_failures
            )

    def _analyze_with_llm(self, task_log, first_failure, worst_failure,
                          evaluation, similar_failures) -> RootCauseAnalysis:
        """使用LLM进行深度根因分析"""
        prompt = self._build_analysis_prompt(
            task_log, first_failure, worst_failure,
            evaluation, similar_failures
        )

        response = self.llm.chat(prompt)

        # 解析LLM回复（期望JSON格式）
        return self._parse_llm_response(response)

    def _analyze_with_rules(self, task_log, first_failure, worst_failure,
                            similar_failures) -> RootCauseAnalysis:
        """规则-based降级分析（无LLM时使用）"""
        obs = first_failure.get("observation", "")
        retries = worst_failure.get("retry_count", 0)

        # 根据观察内容推断失败层级
        layer, cause = self._infer_layer_from_observation(obs)

        # 查看类似历史失败
        historical_solution = ""
        if similar_failures:
            for sf in similar_failures[:2]:
                if sf.get("solution"):
                    historical_solution += f"历史解决方案: {sf['solution']}\n"

        return RootCauseAnalysis(
            failure_layer=layer,
            root_cause=cause,
            evidence=obs,
            solution=historical_solution or self._suggest_rule_solution(layer, cause),
            avoidance=f"避免{cause}：确保{layer}层准确可靠",
            confidence=0.5,  # 规则分析置信度较低
        )

    def _infer_layer_from_observation(self, observation: str) -> tuple[str, str]:
        """从观察内容推断失败层级"""
        obs_lower = observation.lower()

        # 感知层失败的常见关键词
        if any(kw in obs_lower for kw in ["找不到", "未发现", "看不到", "元素不存在",
                                           "not found", "invisible", "timeout等待"]):
            return "感知", f"未能感知到目标元素/状态: {observation}"

        # 行动层失败的常见关键词
        if any(kw in obs_lower for kw in ["点击失败", "操作失败", "权限不足",
                                           "执行错误", "click fail", "permission"]):
            return "行动", f"执行操作失败: {observation}"

        # 决策层失败的常见关键词
        if any(kw in obs_lower for kw in ["选择错误", "路径错误", "误操作",
                                           "wrong path", "incorrect"]):
            return "决策", f"决策选择错误: {observation}"

        # 思考层失败的常见关键词
        if any(kw in obs_lower for kw in ["逻辑错误", "推断失败", "条件遗漏",
                                           "判断错误", "logic error"]):
            return "思考", f"推理推断错误: {observation}"

        # 默认
        return "行动", observation

    def _suggest_rule_solution(self, layer: str, cause: str) -> str:
        """基于失败层级的通用解决建议"""
        solutions = {
            "感知": "增加等待时间、扩大搜索范围、添加备选定位策略",
            "思考": "细化推理规则、添加条件检查、引入多步验证",
            "决策": "增加路径评估、设置优先级规则、添加回退策略",
            "行动": "优化操作时序、增加重试机制、检查权限设置",
        }
        return solutions.get(layer, "增加重试和备选方案")

    def _build_analysis_prompt(self, task_log, first_failure, worst_failure,
                               evaluation, similar_failures) -> str:
        """构建给LLM的分析prompt"""
        parts = [
            "# 任务失败根因分析",
            f"## 任务目标\n{task_log.get('goal', '未知')}",
            f"\n## 任务统计",
            f"- 总步骤数: {task_log.get('summary', {}).get('total_steps', 0)}",
            f"- 成功步骤: {task_log.get('summary', {}).get('success_count', 0)}",
            f"- 总重试次数: {task_log.get('summary', {}).get('total_retries', 0)}",
        ]

        if evaluation:
            parts.append(f"\n## 评估结果")
            parts.append(f"- 效率: {evaluation.get('efficiency', 0):.2f}")
            parts.append(f"- 重试率: {evaluation.get('retry_rate', 0):.2f}")
            parts.append(f"- 覆盖率: {evaluation.get('coverage', 0):.2f}")

        parts.append(f"\n## 第一个失败步骤")
        parts.append(f"- 步骤ID: {first_failure.get('step_id')}")
        parts.append(f"- 描述: {first_failure.get('description')}")
        parts.append(f"- 动作: {first_failure.get('action')}")
        parts.append(f"- 观察结果: {first_failure.get('observation')}")
        parts.append(f"- 重试次数: {first_failure.get('retry_count', 0)}")

        if similar_failures:
            parts.append(f"\n## 历史类似失败案例")
            for sf in similar_failures[:3]:
                parts.append(f"- 根因: {sf.get('root_cause', '未知')} | 解决: {sf.get('solution', '暂无')}")

        parts.append("""
\n## 分析要求
请按JSON格式输出根因分析：
```json
{
  "failure_layer": "感知/思考/决策/行动 中的一个",
  "root_cause": "根因的详细描述",
  "evidence": "支撑此判断的证据",
  "solution": "具体的解决方案",
  "avoidance": "如何在未来避免类似问题",
  "confidence": 0.0~1.0的分析置信度
}
```

失败层级定义：
- 感知: Agent未能正确感知环境/UI状态
- 思考: Agent推理推断出错
- 决策: Agent选择了错误的行动方案
- 行动: Agent执行操作本身失败
""")

        return "\n".join(parts)

    def _parse_llm_response(self, response: str) -> RootCauseAnalysis:
        """解析LLM回复为RootCauseAnalysis"""
        # 尝试提取JSON
        import json
        import re

        json_match = re.search(r'\{[^{}]+\}', response, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                layer = data.get("failure_layer", "未知")
                if layer not in FAILURE_LAYERS:
                    layer = "未知"
                return RootCauseAnalysis(
                    failure_layer=layer,
                    root_cause=data.get("root_cause", ""),
                    evidence=data.get("evidence", ""),
                    solution=data.get("solution", ""),
                    avoidance=data.get("avoidance", ""),
                    confidence=float(data.get("confidence", 0.5)),
                )
            except json.JSONDecodeError:
                pass

        # JSON解析失败 → 降级为规则分析
        return RootCauseAnalysis(
            failure_layer="未知",
            root_cause=response[:200],
            evidence="LLM回复解析失败",
            solution="检查LLM输出格式",
            avoidance="确保LLM prompt要求JSON输出",
            confidence=0.3,
        )

    def _analyze_incomplete(self, task_log, evaluation) -> RootCauseAnalysis:
        """分析子任务未完全覆盖的情况"""
        completed = task_log.get("summary", {}).get("success_count", 0)
        total = len(task_log.get("sub_tasks", []))

        return RootCauseAnalysis(
            failure_layer="决策",
            root_cause=f"只完成了{completed}/{total}个子任务，未达成完整目标",
            evidence=f"覆盖率: {evaluation.get('coverage', 0):.2f}",
            solution="检查后续子任务是否需要调整执行策略",
            avoidance="规划时确保子任务之间的依赖关系和优先级",
            confidence=0.6,
        )