"""兼容性测试模块 - 提供多设备兼容性测试能力"""

from .artifact_store import artifact_store
from .assertions import (
    AssertionDimension,
    AssertionStatus,
    ErrorCategory,
    SeverityLevel,
    validate_dimension,
    validate_error_category,
    validate_severity,
    validate_status,
)
from .device_lock import device_lock_registry
from .event_parser import (
    AssertionEvent,
    AssertionSummary,
    EventParser,
    summarize_assertions,
)
from .report_builder import report_builder
from .scheduler import compatibility_scheduler
from .vlm_ui_analyzer import VLMUIAnalyzer

__all__ = [
    # 断言相关
    "AssertionDimension",
    "AssertionStatus",
    "ErrorCategory",
    "SeverityLevel",
    "validate_dimension",
    "validate_error_category",
    "validate_severity",
    "validate_status",
    # 事件解析
    "AssertionEvent",
    "AssertionSummary",
    "EventParser",
    "summarize_assertions",
    # VLM UI 分析
    "VLMUIAnalyzer",
    # 组件实例
    "device_lock_registry",
    "compatibility_scheduler",
    "artifact_store",
    "report_builder",
]
