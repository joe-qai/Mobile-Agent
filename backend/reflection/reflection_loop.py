"""
反思循环（Reflection Loop）
- 串联 评估 → 分析 → 提炼 的完整反思流程
- 提供统一的反思接口，任务完成后一键触发
- 自动将反思结果写入短期+长期记忆
"""

from .analyzer import RootCauseAnalysis, RootCauseAnalyzer
from .distiller import KnowledgeDistiller
from .evaluator import TaskEvaluation, TaskEvaluator


class ReflectionResult:
    """反思结果汇总"""
    def __init__(self, evaluation: dict, analysis: dict, distilled: dict):
        self.evaluation = evaluation       # 评估结果
        self.analysis = analysis           # 根因分析
        self.distilled = distilled         # 提炼的知识

    def to_dict(self) -> dict:
        return {
            "success": self.evaluation.get("success", False),
            "grade": self.evaluation.get("grade", "未知"),
            "evaluation": self.evaluation,
            "failure_analysis": self.analysis,
            "strategy": self.distilled.get("strategy"),
            "failure_pattern": self.distilled.get("failure"),
            "ui_knowledge": self.distilled.get("ui_knowledge"),
        }


class ReflectionLoop:
    """
    反思循环：任务完成后的完整反思流程
    
    流程：
    1. evaluate  → 量化评估任务质量
    2. analyze   → LLM/规则分析失败根因
    3. distill   → 提炼可复用知识
    4. save      → 写入短期+长期记忆
    
    用法：
        reflector = ReflectionLoop(
            evaluator=TaskEvaluator(),
            analyzer=RootCauseAnalyzer(llm_client),
            distiller=KnowledgeDistiller(llm_client),
            short_term=stm,
            long_term=ltm,
        )
        
        result = reflector.reflect(task_log)
        # result 已自动写入短期+长期记忆
    """

    def __init__(self, evaluator=None, analyzer=None, distiller=None,
                 short_term=None, long_term=None):
        self.evaluator = evaluator or TaskEvaluator()
        self.analyzer = analyzer or RootCauseAnalyzer()
        self.distiller = distiller or KnowledgeDistiller()
        self.short_term = short_term  # ShortTermMemory
        self.long_term = long_term    # LongTermMemory

    def reflect(self, task_log: dict) -> ReflectionResult:
        """
        执行完整反思流程
        
        Args:
            task_log: WorkingMemory.end_task() 返回的任务日志
        Returns:
            ReflectionResult 反思结果（已自动写入记忆）
        """
        # Step 1: 评估
        evaluation_obj = self.evaluator.evaluate(task_log)
        evaluation = evaluation_obj.to_dict()
        evaluation["grade"] = evaluation_obj.get_grade()

        # Step 2: 分析根因（如果任务不成功）
        similar_failures = []
        if self.short_term and not evaluation["success"]:
            # 从短期记忆查找类似失败（辅助分析）
            for step in task_log.get("steps", []):
                if not step.get("success", True):
                    similar = self.short_term.find_similar_failures(
                        step.get("observation", ""), limit=3
                    )
                    similar_failures.extend(similar)

        if evaluation["success"]:
            # 成功任务不需要根因分析
            analysis = {
                "failure_layer": "",
                "root_cause": "",
                "evidence": "任务成功完成",
                "solution": "",
                "avoidance": "",
                "confidence": 1.0,
            }
        else:
            analysis_obj = self.analyzer.analyze(
                task_log, evaluation, similar_failures
            )
            analysis = analysis_obj.to_dict()

        # Step 3: 提炼知识
        distilled = self.distiller.distill(task_log, evaluation, analysis)
        distilled_dict = {}
        if distilled.get("strategy"):
            distilled_dict["strategy"] = distilled["strategy"].to_dict()
        if distilled.get("failure"):
            distilled_dict["failure"] = distilled["failure"].to_dict()
        distilled_dict["ui_knowledge"] = distilled.get("ui_knowledge")

        # Step 4: 写入记忆
        self._save_to_memory(task_log, evaluation, analysis, distilled)

        return ReflectionResult(evaluation, analysis, distilled_dict)

    def _save_to_memory(self, task_log: dict, evaluation: dict,
                        analysis: dict, distilled: dict):
        """将反思结果写入短期+长期记忆"""

        # → 短期记忆：记录任务日志
        if self.short_term:
            self.short_term.record_task(task_log)

            # 更新失败分析（如果有）
            if not evaluation.get("success") and analysis.get("root_cause"):
                for step in task_log.get("steps", []):
                    if not step.get("success", True):
                        self.short_term.update_failure_analysis(
                            task_id=task_log.get("task_id", ""),
                            step_id=step.get("step_id", ""),
                            error_type=analysis.get("failure_layer", "未知"),
                            root_cause=analysis.get("root_cause", ""),
                            solution=analysis.get("solution", ""),
                        )

        # → 长期记忆：学习提炼的知识
        if self.long_term:
            reflection_for_ltm = {
                "success": evaluation.get("success", False),
                "strategy": distilled_dict if distilled.get("strategy") else None,
                "failure_analysis": distilled_dict if distilled.get("failure") else None,
                "ui_knowledge": distilled.get("ui_knowledge"),
            }

            # 构造给long_term的格式
            ltm_data = {
                "success": evaluation.get("success", False),
                "strategy": distilled.get("strategy").to_dict() if distilled.get("strategy") else None,
                "failure_analysis": distilled.get("failure").to_dict() if distilled.get("failure") else None,
                "ui_knowledge": distilled.get("ui_knowledge"),
            }
            self.long_term.learn_from_reflection(ltm_data)