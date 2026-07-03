"""
任务评估器（Task Evaluator）
- 量化评估任务执行质量
- 输出评估指标：成功率、效率、重试率、覆盖率
"""

import time
from typing import Dict


class TaskEvaluation:
    """任务评估结果"""
    def __init__(self, success: bool, efficiency: float, retry_rate: float,
                 coverage: float, duration_ms: float, details: dict = None):
        self.success = success           # 任务是否成功完成
        self.efficiency = efficiency     # 效率（最优步骤数/实际步骤数）
        self.retry_rate = retry_rate     # 重试率（重试次数/总步骤数）
        self.coverage = coverage         # 子任务覆盖率
        self.duration_ms = duration_ms   # 总耗时
        self.details = details or {}     # 详细评估

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "efficiency": self.efficiency,
            "retry_rate": self.retry_rate,
            "coverage": self.coverage,
            "duration_ms": self.duration_ms,
            "details": self.details,
        }

    def get_grade(self) -> str:
        """
        评定等级：
        - 优秀：效率>0.8，无重试，全覆盖
        - 良好：效率>0.6，重试<0.2，覆盖率>0.8
        - 一般：效率>0.4，重试<0.4，覆盖率>0.5
        - 较差：其他
        """
        if self.success and self.efficiency >= 0.8 and self.retry_rate == 0 and self.coverage >= 1.0:
            return "优秀"
        elif self.efficiency >= 0.6 and self.retry_rate <= 0.2 and self.coverage >= 0.8:
            return "良好"
        elif self.efficiency >= 0.4 and self.retry_rate <= 0.4 and self.coverage >= 0.5:
            return "一般"
        else:
            return "较差"


class TaskEvaluator:
    """
    任务执行质量评估器
    
    用法：
        evaluator = TaskEvaluator()
        evaluation = evaluator.evaluate(task_log)
        print(evaluation.get_grade())  # "优秀"/"良好"/"一般"/"较差"
    """

    def evaluate(self, task_log: dict) -> TaskEvaluation:
        """
        量化评估一次任务执行
        
        Args:
            task_log: WorkingMemory.end_task() 返回的任务日志
        Returns:
            TaskEvaluation 评估结果
        """
        summary = task_log.get("summary", {})
        steps = task_log.get("steps", [])

        total_steps = summary.get("total_steps", len(steps))
        success_count = summary.get("success_count", 0)
        total_retries = summary.get("total_retries", 0)
        sub_tasks = task_log.get("sub_tasks", [])
        completed_idx = summary.get("completed_steps", success_count)

        # 1. 成功与否
        is_success = summary.get("is_completed", False) and success_count == total_steps

        # 2. 效率 = 最优步骤数 / 实际步骤数
        # 最优步骤数 = 子任务数量（理想情况下每步成功）
        optimal_steps = len(sub_tasks)
        actual_steps = max(total_steps, 1)
        efficiency = optimal_steps / actual_steps if optimal_steps > 0 else 0

        # 3. 重试率 = 总重试次数 / 总步骤数
        retry_rate = total_retries / max(total_steps, 1)

        # 4. 子任务覆盖率
        coverage = completed_idx / max(len(sub_tasks), 1)

        # 5. 总耗时
        duration_ms = summary.get("total_duration_ms", 0)

        # 6. 详细分析
        details = self._analyze_details(task_log)

        return TaskEvaluation(
            success=is_success,
            efficiency=min(efficiency, 1.0),  # 上限1.0
            retry_rate=retry_rate,
            coverage=min(coverage, 1.0),
            duration_ms=duration_ms,
            details=details,
        )

    def _analyze_details(self, task_log: dict) -> dict:
        """详细分析任务执行细节"""
        steps = task_log.get("steps", [])
        details = {
            "total_steps": len(steps),
            "failed_steps": [],
            "retry_steps": [],
            "slow_steps": [],
        }

        for step in steps:
            if not step.get("success", True):
                details["failed_steps"].append({
                    "step_id": step.get("step_id"),
                    "description": step.get("description"),
                    "observation": step.get("observation"),
                    "retry_count": step.get("retry_count", 0),
                })

            if step.get("retry_count", 0) > 0:
                details["retry_steps"].append({
                    "step_id": step.get("step_id"),
                    "retry_count": step.get("retry_count"),
                })

            # 耗时超过平均2倍的步骤
            if step.get("duration_ms", 0) > 0:
                details["slow_steps"].append({
                    "step_id": step.get("step_id"),
                    "duration_ms": step.get("duration_ms"),
                })

        return details