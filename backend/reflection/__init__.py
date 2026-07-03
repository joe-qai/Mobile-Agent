"""
反思学习模块：自我评估 → 原因分析 → 知识沉淀

核心流程：
1. evaluate()   — 量化评估任务执行质量
2. analyze()    — LLM深度分析根因
3. distill()    — 提炼可复用知识写入记忆

主动学习组件：
- ReactiveReflexor  — 主动学习主组件（集成评估、学习、预测）
- RealTimeEvaluator — 轻量级实时评估器
- IncrementalLearner — 增量学习器
- FailurePredictor  — 失败预测器
"""

from .analyzer import RootCauseAnalyzer
from .distiller import KnowledgeDistiller
from .evaluator import TaskEvaluator
from .failure_predictor import FailurePredictor
from .incremental_learner import IncrementalLearner
from .reactive_reflexor import ReactiveReflexor
from .real_time_evaluator import RealTimeEvaluator
from .reflection_loop import ReflectionLoop

__all__ = [
    "TaskEvaluator",
    "RootCauseAnalyzer",
    "KnowledgeDistiller",
    "ReflectionLoop",
    "ReactiveReflexor",
    "RealTimeEvaluator",
    "IncrementalLearner",
    "FailurePredictor",
]