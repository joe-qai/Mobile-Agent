"""事件解析器 - 从脚本stdout中提取JSONL事件并汇总断言"""
import json
import re
from typing import Any, Dict, List, Optional

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


def safe_json_parse(json_str: str) -> Optional[Dict[str, Any]]:
    """
    健壮的 JSON 解析函数，支持多层回退策略
    
    Args:
        json_str: 待解析的 JSON 字符串
        
    Returns:
        解析后的字典，解析失败返回 None
    """
    if not json_str or not isinstance(json_str, str):
        return None
    
    json_str = json_str.strip()
    
    # 策略 1: 直接解析
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass
    
    # 策略 2: 搜索代码块中的 JSON
    code_block_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', json_str)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1).strip())
        except json.JSONDecodeError:
            pass
    
    # 策略 3: 查找第一个完整的 JSON 对象
    brace_count = 0
    start_idx = -1
    for i, char in enumerate(json_str):
        if char == '{':
            if brace_count == 0:
                start_idx = i
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0 and start_idx != -1:
                try:
                    return json.loads(json_str[start_idx:i+1])
                except json.JSONDecodeError:
                    start_idx = -1
    
    # 策略 4: 查找第一个完整的 JSON 数组
    bracket_count = 0
    start_idx = -1
    for i, char in enumerate(json_str):
        if char == '[':
            if bracket_count == 0:
                start_idx = i
            bracket_count += 1
        elif char == ']':
            bracket_count -= 1
            if bracket_count == 0 and start_idx != -1:
                try:
                    return json.loads(json_str[start_idx:i+1])
                except json.JSONDecodeError:
                    start_idx = -1
    
    return None


class AssertionEvent:
    """断言事件"""
    
    def __init__(self, event_data: Dict[str, Any]):
        self.type: str = event_data.get("type", "")
        self.dimension: Optional[AssertionDimension] = validate_dimension(event_data.get("dimension", ""))
        self.name: str = event_data.get("name", "")
        self.status: Optional[AssertionStatus] = validate_status(event_data.get("status", ""))
        self.target: str = event_data.get("target", "")
        self.message: str = event_data.get("message", "")
        self.severity: Optional[SeverityLevel] = validate_severity(event_data.get("severity", ""))
        self.step_index: Optional[int] = event_data.get("step_index")
        self.evidence: Dict[str, Any] = event_data.get("evidence", {})
    
    def is_valid(self) -> bool:
        """检查断言事件是否有效"""
        return (
            self.type == "assertion"
            and self.dimension is not None
            and self.name
            and self.status is not None
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "type": self.type,
            "dimension": self.dimension.value if self.dimension else "",
            "name": self.name,
            "status": self.status.value if self.status else "",
            "target": self.target,
            "message": self.message,
            "severity": self.severity.value if self.severity else "",
            "step_index": self.step_index,
            "evidence": self.evidence,
        }


class StepEvent:
    """步骤事件"""
    
    def __init__(self, event_data: Dict[str, Any]):
        self.type: str = event_data.get("type", "")
        self.index: Optional[int] = event_data.get("index")
        self.action: str = event_data.get("action", "")
        self.message: str = event_data.get("message", "")
    
    def is_valid(self) -> bool:
        """检查步骤事件是否有效"""
        return self.type == "step" and self.index is not None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "type": self.type,
            "index": self.index,
            "action": self.action,
            "message": self.message,
        }


class ErrorEvent:
    """错误事件"""
    
    def __init__(self, event_data: Dict[str, Any]):
        self.type: str = event_data.get("type", "")
        self.category: Optional[ErrorCategory] = validate_error_category(event_data.get("category", ""))
        self.message: str = event_data.get("message", "")
        self.step_index: Optional[int] = event_data.get("step_index")
    
    def is_valid(self) -> bool:
        """检查错误事件是否有效"""
        return self.type == "error"
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "type": self.type,
            "category": self.category.value if self.category else "",
            "message": self.message,
            "step_index": self.step_index,
        }


class ScreenshotEvent:
    """截图事件"""
    
    def __init__(self, event_data: Dict[str, Any]):
        self.type: str = event_data.get("type", "")
        self.path: str = event_data.get("path", "")
        self.relative_path: str = event_data.get("relative_path", "")
        self.kind: str = event_data.get("kind", "")
        self.step_index: Optional[int] = event_data.get("step_index")
        self.assertion_name: str = event_data.get("assertion_name", "")
    
    def is_valid(self) -> bool:
        """检查截图事件是否有效"""
        return self.type == "screenshot" and (self.path or self.relative_path)
    
    def get_effective_path(self) -> str:
        """获取有效的路径（优先使用relative_path）"""
        return self.relative_path or self.path
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "type": self.type,
            "path": self.path,
            "relative_path": self.relative_path,
            "kind": self.kind,
            "step_index": self.step_index,
            "assertion_name": self.assertion_name,
        }


class CaptureEvent:
    """截图捕获事件（延迟VLM分析用）"""
    
    def __init__(self, event_data: Dict[str, Any]):
        self.type: str = event_data.get("type", "")
        self.timestamp: float = event_data.get("timestamp", 0.0)
        self.step_name: str = event_data.get("step_name", "")
        self.device_id: str = event_data.get("device_id", "")
        self.image_size: int = event_data.get("image_size", 0)
    
    def is_valid(self) -> bool:
        """检查捕获事件是否有效"""
        return self.type == "capture" and self.image_size > 0
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "step_name": self.step_name,
            "device_id": self.device_id,
            "image_size": self.image_size,
        }


class ArtifactEvent:
    """产物事件"""
    
    def __init__(self, event_data: Dict[str, Any]):
        self.type: str = event_data.get("type", "")
        self.artifact_type: str = event_data.get("artifact_type", "")
        self.path: str = event_data.get("path", "")
        self.relative_path: str = event_data.get("relative_path", "")
        self.assertion_name: str = event_data.get("assertion_name", "")
        self.step_index: Optional[int] = event_data.get("step_index")
    
    def is_valid(self) -> bool:
        """检查产物事件是否有效"""
        return self.type == "artifact" and self.artifact_type and (self.path or self.relative_path)
    
    def get_effective_path(self) -> str:
        """获取有效的路径（优先使用relative_path）"""
        return self.relative_path or self.path
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "type": self.type,
            "artifact_type": self.artifact_type,
            "path": self.path,
            "relative_path": self.relative_path,
            "assertion_name": self.assertion_name,
            "step_index": self.step_index,
        }


class EventParser:
    """事件解析器"""
    
    # JSON行的正则模式
    JSON_LINE_PATTERN = re.compile(r'^\s*\{.*\}\s*$')
    # 捕获事件模式 [CAPTURE_EVENT] {"type": "capture", ...}
    CAPTURE_EVENT_PATTERN = re.compile(r'\[CAPTURE_EVENT\]\s*(\{.*\})')
    
    @classmethod
    def parse_line(cls, line: str) -> Optional[Dict[str, Any]]:
        """
        解析单行输出，判断是否为JSON事件
        
        Args:
            line: 单行输出
        
        Returns:
            解析后的JSON对象，如果不是有效的JSON事件则返回None
        """
        line = line.strip()
        if not line:
            return None
        
        # 检查是否为捕获事件格式
        capture_match = cls.CAPTURE_EVENT_PATTERN.search(line)
        if capture_match:
            json_str = capture_match.group(1)
            event_data = safe_json_parse(json_str)
            if event_data and isinstance(event_data, dict) and "type" in event_data:
                return event_data
        
        # 检查是否以{开头
        if not line.startswith("{"):
            return None
        
        event_data = safe_json_parse(line)
        if event_data and isinstance(event_data, dict) and "type" in event_data:
            return event_data
        return None
    
    @classmethod
    def parse_output(cls, stdout: str) -> List[Dict[str, Any]]:
        """
        解析脚本输出，提取所有JSON事件
        
        Args:
            stdout: 脚本标准输出
        
        Returns:
            JSON事件列表
        """
        if not stdout:
            return []
        
        events = []
        for line in stdout.split("\n"):
            event = cls.parse_line(line)
            if event:
                events.append(event)
        return events
    
    @classmethod
    def parse_assertion(cls, event_data: Dict[str, Any]) -> Optional[AssertionEvent]:
        """解析断言事件"""
        if event_data.get("type") != "assertion":
            return None
        
        event = AssertionEvent(event_data)
        return event if event.is_valid() else None
    
    @classmethod
    def parse_step(cls, event_data: Dict[str, Any]) -> Optional[StepEvent]:
        """解析步骤事件"""
        if event_data.get("type") != "step":
            return None
        
        event = StepEvent(event_data)
        return event if event.is_valid() else None
    
    @classmethod
    def parse_error(cls, event_data: Dict[str, Any]) -> Optional[ErrorEvent]:
        """解析错误事件"""
        if event_data.get("type") != "error":
            return None
        
        event = ErrorEvent(event_data)
        return event if event.is_valid() else None
    
    @classmethod
    def parse_screenshot(cls, event_data: Dict[str, Any]) -> Optional[ScreenshotEvent]:
        """解析截图事件"""
        if event_data.get("type") != "screenshot":
            return None
        
        event = ScreenshotEvent(event_data)
        return event if event.is_valid() else None
    
    @classmethod
    def parse_artifact(cls, event_data: Dict[str, Any]) -> Optional[ArtifactEvent]:
        """解析产物事件"""
        if event_data.get("type") != "artifact":
            return None
        
        event = ArtifactEvent(event_data)
        return event if event.is_valid() else None


class AssertionSummary:
    """断言汇总"""
    
    def __init__(self):
        self.total: int = 0
        self.passed: int = 0
        self.failed: int = 0
        self.skipped: int = 0
        self.warning: int = 0
        self.pending_review: int = 0
        self.by_dimension: Dict[str, Dict[str, int]] = {
            dim.value: {"total": 0, "passed": 0, "failed": 0, "warning": 0, "pending_review": 0}
            for dim in AssertionDimension
        }
    
    def add_assertion(self, assertion: AssertionEvent):
        """添加断言到汇总"""
        if not assertion.is_valid():
            return
        
        self.total += 1
        
        dim_key = assertion.dimension.value
        self.by_dimension[dim_key]["total"] += 1
        
        if assertion.status == AssertionStatus.PASSED:
            self.passed += 1
            self.by_dimension[dim_key]["passed"] += 1
        elif assertion.status == AssertionStatus.FAILED:
            self.failed += 1
            self.by_dimension[dim_key]["failed"] += 1
        elif assertion.status == AssertionStatus.WARNING:
            self.warning += 1
            self.by_dimension[dim_key]["warning"] += 1
        elif assertion.status == AssertionStatus.SKIPPED:
            self.skipped += 1
        elif assertion.status == AssertionStatus.PENDING_REVIEW:
            self.pending_review += 1
            self.by_dimension[dim_key]["pending_review"] += 1
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "warning": self.warning,
            "pending_review": self.pending_review,
            "skipped": self.skipped,
            "by_dimension": self.by_dimension,
        }


def summarize_assertions(events: List[Dict[str, Any]]) -> AssertionSummary:
    """
    从事件列表中汇总断言
    
    Args:
        events: 事件列表
    
    Returns:
        断言汇总
    """
    summary = AssertionSummary()
    
    for event_data in events:
        if event_data.get("type") == "assertion":
            assertion = AssertionEvent(event_data)
            summary.add_assertion(assertion)
    
    return summary
