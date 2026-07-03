"""
ReactiveReflexor - 主动学习主组件
- 集成实时评估、增量学习、失败预测
- 提供 reflect() 统一入口
"""

from typing import Any, Dict, Optional

from .failure_predictor import FailurePredictor
from .incremental_learner import IncrementalLearner
from .real_time_evaluator import RealTimeEvaluator


class ReactiveReflexor:
    """
    主动学习主组件
    
    集成三个子组件：
    - RealTimeEvaluator: 轻量级实时评估
    - IncrementalLearner: 增量学习
    - FailurePredictor: 失败预测
    """
    
    def __init__(self, short_term=None, long_term=None):
        """
        初始化主动学习组件
        
        Args:
            short_term: ShortTermMemory 实例
            long_term: LongTermMemory 实例
        """
        self.evaluator = RealTimeEvaluator()
        self.learner = IncrementalLearner(short_term=short_term)
        self.predictor = FailurePredictor(short_term=short_term, long_term=long_term)
        
        self.short_term = short_term
        self.long_term = long_term
    
    def quick_evaluate(self, step_result: Dict[str, Any]) -> str:
        """
        快速评估步骤结果
        
        Args:
            step_result: 步骤执行结果
        
        Returns:
            评估状态: "success" / "suspicious" / "failed"
        """
        return self.evaluator.quick_evaluate(step_result)
    
    def incremental_learn(self, step_result: Dict[str, Any], context: Dict[str, Any]):
        """
        增量学习
        
        Args:
            step_result: 步骤执行结果
            context: 执行上下文
        """
        if step_result.get("success"):
            self.learner.learn_on_success(step_result, context)
        else:
            self.learner.learn_on_failure(step_result, context)
    
    def predict_failures(self, step_description: str, context: Dict[str, Any]) -> list:
        """
        预测可能的失败
        
        Args:
            step_description: 步骤描述
            context: 执行上下文
        
        Returns:
            预测结果列表
        """
        return self.predictor.predict(step_description, context)
    
    def get_risk_level(self, step_description: str, context: Dict[str, Any]) -> str:
        """
        获取风险等级
        
        Args:
            step_description: 步骤描述
            context: 执行上下文
        
        Returns:
            风险等级: "low" / "medium" / "high"
        """
        return self.predictor.get_risk_level(step_description, context)
    
    def reflect(self, step_result: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """
        统一反思入口
        
        执行完整反思流程：
        1. 评估步骤结果
        2. 预测可能的失败
        3. 增量学习
        
        Args:
            step_result: 步骤执行结果
            context: 执行上下文
        
        Returns:
            反思结果，包含:
            - evaluation: 评估结果
            - predictions: 失败预测
            - learning: 学习结果
        """
        # 1. 评估
        evaluation = self.evaluator.get_evaluation_metrics(step_result)
        
        # 2. 预测
        step_description = self._describe_step(step_result)
        predictions = self.predict_failures(step_description, context)
        
        # 3. 学习
        self.incremental_learn(step_result, context)
        
        return {
            "evaluation": evaluation,
            "predictions": predictions,
            "learning": {
                "status": "recorded" if self.learner._is_noteworthy(step_result) else "skipped",
                "step_description": step_description,
            },
        }
    
    def get_failure_solutions(self, step_result: Dict[str, Any]) -> list:
        """
        获取失败解决方案
        
        Args:
            step_result: 失败的步骤结果
        
        Returns:
            解决方案列表
        """
        return self.predictor.get_failure_solutions(step_result)
    
    def _describe_step(self, step_result: Dict[str, Any]) -> str:
        """
        生成步骤描述
        
        Args:
            step_result: 步骤执行结果
        
        Returns:
            步骤描述字符串
        """
        action = step_result.get("action", "未知操作")
        target = step_result.get("target", "未知目标")
        return f"{action} {target}"
