"""
增量学习器（Incremental Learner）
- 成功后立即提炼策略片段
- 与 KnowledgeDistiller 的区别：实时片段 vs 任务后整体提炼
"""

import time
from typing import Any, Dict, Optional


class IncrementalLearner:
    """
    增量学习器：成功后立即提炼策略
    
    与 KnowledgeDistiller 的区别：
    - KnowledgeDistiller：任务后整体提炼
    - IncrementalLearner：实时片段提炼
    """
    
    def __init__(self, short_term=None):
        """
        初始化增量学习器
        
        Args:
            short_term: ShortTermMemory 实例
        """
        self.short_term = short_term
    
    def learn_on_success(self, step_result: Dict[str, Any], context: Dict[str, Any]):
        """
        成功时学习
        
        Args:
            step_result: 步骤执行结果
            context: 执行上下文
        """
        # 只记录值得记录的步骤
        if not self._is_noteworthy(step_result):
            return
        
        # 生成策略片段
        fragment = self._generate_fragment(step_result, context)
        
        # 记录到短期记忆
        if self.short_term:
            self.short_term.record_strategy_fragment(fragment)
    
    def learn_on_failure(self, step_result: Dict[str, Any], context: Dict[str, Any]):
        """
        失败时学习
        
        Args:
            step_result: 步骤执行结果
            context: 执行上下文
        """
        # 失败时记录失败模式
        failure_pattern = self._extract_failure_pattern(step_result, context)
        
        if self.short_term and failure_pattern:
            self.short_term.record_failure_pattern(failure_pattern)
    
    def _is_noteworthy(self, step_result: Dict[str, Any]) -> bool:
        """
        判断步骤是否值得记录
        
        值得记录的条件：
        1. 有思考过程（thinking）
        2. 有重试（retry_count > 0）
        3. 有特殊处理逻辑
        """
        # 有思考过程
        if step_result.get("thinking"):
            return True
        
        # 有重试
        if step_result.get("retry_count", 0) > 0:
            return True
        
        # 有特殊处理标记
        if step_result.get("noteworthy"):
            return True
        
        return False
    
    def _generate_fragment(self, step_result: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """
        生成策略片段
        
        Args:
            step_result: 步骤执行结果
            context: 执行上下文
        
        Returns:
            策略片段字典
        """
        return {
            "step_description": self._describe_step(step_result),
            "thinking": step_result.get("thinking", ""),
            "action": step_result.get("action", ""),
            "target": step_result.get("target", ""),
            "context": {
                "task_goal": context.get("task_goal", ""),
                "device": context.get("device", ""),
                "app": context.get("app", ""),
            },
            "confidence": self._calculate_confidence(step_result),
            "timestamp": time.time(),
        }
    
    def _extract_failure_pattern(self, step_result: Dict[str, Any], context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        提取失败模式
        
        Args:
            step_result: 步骤执行结果
            context: 执行上下文
        
        Returns:
            失败模式字典，或 None
        """
        observation = step_result.get("observation", "")
        if not observation:
            return None
        
        return {
            "error_description": observation,
            "step_description": self._describe_step(step_result),
            "root_cause": step_result.get("root_cause", "未知"),
            "solution": step_result.get("solution", ""),
            "context": {
                "task_goal": context.get("task_goal", ""),
                "device": context.get("device", ""),
            },
            "timestamp": time.time(),
        }
    
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
    
    def _calculate_confidence(self, step_result: Dict[str, Any]) -> float:
        """
        计算策略置信度
        
        Args:
            step_result: 步骤执行结果
        
        Returns:
            置信度 (0-1)
        """
        base_confidence = 0.7
        
        # 无重试增加置信度
        retry_count = step_result.get("retry_count", 0)
        if retry_count == 0:
            base_confidence += 0.2
        elif retry_count == 1:
            base_confidence += 0.1
        
        # 有思考过程增加置信度
        if step_result.get("thinking"):
            base_confidence += 0.1
        
        return min(1.0, base_confidence)
