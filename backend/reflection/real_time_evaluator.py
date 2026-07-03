"""
轻量级实时评估器（Real-time Evaluator）
- 无需 LLM 调用，纯规则计算
- 输出简化指标：success/suspicious/failed
- 用于 Agent 快速决策
"""

from typing import Any, Dict


class RealTimeEvaluator:
    """
    轻量级实时评估器
    
    特点：
    - 无需 LLM 调用，纯规则计算
    - 输出简化指标：success/suspicious/failed
    - 用于 Agent 快速决策
    """
    
    # 重试阈值配置
    SUSPICIOUS_THRESHOLD = 1  # 重试次数 <= 此值为 suspicious
    FAILED_THRESHOLD = 2      # 重试次数 > 此值为 failed
    
    def quick_evaluate(self, step_result: Dict[str, Any]) -> str:
        """
        快速评估步骤结果
        
        Args:
            step_result: 步骤执行结果，包含:
                - success: bool, 是否成功
                - retry_count: int, 重试次数
                - duration_ms: float, 执行时长（可选）
                - action_type: str, 操作类型（可选）
        
        Returns:
            - "success": 明显成功（无重试）
            - "suspicious": 可疑（有重试但最终成功）
            - "failed": 明显失败
        """
        if step_result.get("success"):
            retry_count = step_result.get("retry_count", 0)
            
            if retry_count == 0:
                return "success"
            elif retry_count <= self.SUSPICIOUS_THRESHOLD:
                return "suspicious"
            else:
                return "failed"
        
        return "failed"
    
    def get_evaluation_metrics(self, step_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        获取完整的评估指标
        
        Args:
            step_result: 步骤执行结果
        
        Returns:
            包含状态和各项指标的字典
        """
        status = self.quick_evaluate(step_result)
        
        # 计算效率分数（0-1，越高越好）
        efficiency_score = self._calculate_efficiency(step_result, status)
        
        return {
            "status": status,
            "retry_count": step_result.get("retry_count", 0),
            "duration_ms": step_result.get("duration_ms", 0),
            "action_type": step_result.get("action_type", "unknown"),
            "efficiency_score": efficiency_score,
        }
    
    def _calculate_efficiency(self, step_result: Dict[str, Any], status: str) -> float:
        """
        计算效率分数
        
        考虑因素：
        - 成功状态
        - 重试次数
        - 执行时长
        """
        if status == "failed":
            return 0.0
        
        base_score = 1.0
        
        # 重试惩罚
        retry_count = step_result.get("retry_count", 0)
        retry_penalty = min(0.3, retry_count * 0.1)
        base_score -= retry_penalty
        
        # 时长惩罚（超过 3 秒开始惩罚）
        duration_ms = step_result.get("duration_ms", 0)
        if duration_ms > 3000:
            duration_penalty = min(0.2, (duration_ms - 3000) / 10000)
            base_score -= duration_penalty
        
        return max(0.0, round(base_score, 2))
