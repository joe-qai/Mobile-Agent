"""断言枚举定义 - 定义验证维度和错误分类"""
from enum import Enum
from typing import Dict, List, Optional


class AssertionDimension(str, Enum):
    """验证维度枚举"""
    LAYOUT = "layout"
    TEXT = "text"
    IMAGE = "image"
    ADAPTATION = "adaptation"
    THEME = "theme"
    PAGE_STATE = "page_state"


class AssertionStatus(str, Enum):
    """断言状态枚举"""
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    WARNING = "warning"
    PENDING_REVIEW = "pending_review"
    UNKNOWN = "unknown"


class SeverityLevel(str, Enum):
    """严重级别枚举"""
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"
    SUGGESTION = "suggestion"


class ErrorCategory(str, Enum):
    """错误分类枚举"""
    DEVICE_DISCONNECTED = "device_disconnected"
    SCRIPT_ERROR = "script_error"
    ASSERTION_FAILED = "assertion_failed"
    TIMEOUT = "timeout"
    SCREENSHOT_UNAVAILABLE = "screenshot_unavailable"
    LAYOUT_MISMATCH = "layout_mismatch"
    TEXT_RENDERING_ERROR = "text_rendering_error"
    IMAGE_RENDERING_ERROR = "image_rendering_error"
    INTERACTION_FEEDBACK_MISSING = "interaction_feedback_missing"
    UNKNOWN = "unknown"


# 维度描述
DIMENSION_DESCRIPTIONS: Dict[AssertionDimension, str] = {
    AssertionDimension.LAYOUT: "布局适配",
    AssertionDimension.TEXT: "文字显示",
    AssertionDimension.IMAGE: "图片显示",
    AssertionDimension.ADAPTATION: "设备适配",
    AssertionDimension.THEME: "主题适配",
    AssertionDimension.PAGE_STATE: "页面状态",
}

# 维度与错误分类映射
DIMENSION_ERROR_CATEGORIES: Dict[AssertionDimension, List[ErrorCategory]] = {
    AssertionDimension.LAYOUT: [ErrorCategory.LAYOUT_MISMATCH],
    AssertionDimension.TEXT: [ErrorCategory.TEXT_RENDERING_ERROR],
    AssertionDimension.IMAGE: [ErrorCategory.IMAGE_RENDERING_ERROR, ErrorCategory.SCREENSHOT_UNAVAILABLE],
    AssertionDimension.ADAPTATION: [ErrorCategory.LAYOUT_MISMATCH],
    AssertionDimension.THEME: [ErrorCategory.TEXT_RENDERING_ERROR],
    AssertionDimension.PAGE_STATE: [ErrorCategory.ASSERTION_FAILED, ErrorCategory.TIMEOUT],
}


def get_default_severity(dimension: AssertionDimension, is_main_flow: bool = False) -> SeverityLevel:
    """获取维度的默认严重级别"""
    if dimension in [AssertionDimension.LAYOUT, AssertionDimension.TEXT, AssertionDimension.IMAGE]:
        return SeverityLevel.MAJOR
    return SeverityLevel.MINOR


def validate_dimension(dimension: str) -> Optional[AssertionDimension]:
    """验证维度字符串是否有效"""
    try:
        return AssertionDimension(dimension)
    except ValueError:
        return None


def validate_status(status: str) -> Optional[AssertionStatus]:
    """验证状态字符串是否有效"""
    try:
        return AssertionStatus(status)
    except ValueError:
        return None


def validate_severity(severity: str) -> Optional[SeverityLevel]:
    """验证严重级别字符串是否有效"""
    try:
        return SeverityLevel(severity)
    except ValueError:
        return None


def validate_error_category(category: str) -> Optional[ErrorCategory]:
    """验证错误分类字符串是否有效"""
    try:
        return ErrorCategory(category)
    except ValueError:
        return None
