"""
失败预测器（Failure Predictor）
- 基于历史模式预测可能的失败
- 与 RootCauseAnalyzer 的区别：事前预测 vs 事后分析
"""

from typing import Any, Dict, List, Optional


class FailurePredictor:
    """
    失败预测器：基于历史模式预测可能的失败
    
    与 RootCauseAnalyzer 的区别：
    - RootCauseAnalyzer：事后分析失败原因
    - FailurePredictor：事前预测可能的失败
    """
    
    def __init__(self, short_term=None, long_term=None):
        """
        初始化失败预测器
        
        Args:
            short_term: ShortTermMemory 实例
            long_term: LongTermMemory 实例
        """
        self.short_term = short_term
        self.long_term = long_term
    
    def predict(self, step_description: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        预测可能的失败
        
        Args:
            step_description: 步骤描述
            context: 执行上下文（task_type, device 等）
        
        Returns:
            预测结果列表，每项包含:
            - pattern: 失败模式名称
            - root_cause: 根因
            - suggestion: 建议解决方案
            - confidence: 置信度 (0-1)
        """
        predictions = []
        
        # 1. 从短期记忆查询类似失败
        if self.short_term:
            similar_failures = self.short_term.find_similar_failures(
                step_description, limit=3
            )
            for failure in similar_failures:
                predictions.append({
                    "pattern": failure.get("error_description", "未知错误"),
                    "root_cause": failure.get("root_cause", ""),
                    "suggestion": failure.get("solution", ""),
                    "confidence": 0.7,  # 短期记忆置信度较低
                    "source": "short_term",
                })
        
        # 2. 从长期记忆查询已知失败模式
        if self.long_term:
            task_type = context.get("task_type", "")
            known_patterns = self.long_term.query_failures(
                task_type=task_type
            )
            for pattern in known_patterns:
                # 避免重复
                if not any(p.get("pattern") == pattern.name for p in predictions):
                    predictions.append({
                        "pattern": pattern.name,
                        "root_cause": pattern.root_cause,
                        "suggestion": pattern.solution,
                        "confidence": min(0.9, 0.5 + pattern.frequency * 0.1),
                        "source": "long_term",
                    })
        
        # 3. 按置信度排序，返回前3个
        predictions.sort(key=lambda x: x.get("confidence", 0), reverse=True)
        return predictions[:3]
    
    def get_failure_solutions(self, step_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        获取失败的解决方案
        
        Args:
            step_result: 失败的步骤结果
        
        Returns:
            解决方案列表
        """
        solutions = []
        observation = step_result.get("observation", "")
        
        if self.short_term:
            similar = self.short_term.find_similar_failures(observation, limit=3)
            for failure in similar:
                if failure.get("solution"):
                    solutions.append({
                        "root_cause": failure.get("root_cause", ""),
                        "solution": failure.get("solution", ""),
                        "confidence": 0.7,
                    })
        
        return solutions
    
    def get_risk_level(self, step_description: str, context: Dict[str, Any]) -> str:
        """
        获取风险等级
        
        Args:
            step_description: 步骤描述
            context: 执行上下文
        
        Returns:
            风险等级: "low" / "medium" / "high"
        """
        predictions = self.predict(step_description, context)
        
        if not predictions:
            return "low"
        
        max_confidence = max(p.get("confidence", 0) for p in predictions)
        
        if max_confidence >= 0.8:
            return "high"
        elif max_confidence >= 0.6:
            return "medium"
        else:
            return "low"
