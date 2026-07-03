"""Pydantic 模型定义"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    """项目创建请求"""
    name: str = Field(..., min_length=1)
    description: str = ""


class ScriptCreate(BaseModel):
    """脚本创建请求"""
    name: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    version: str = "1.0.0"
    project_id: Optional[int] = None
    system_os: str = "Android"


class TaskCreate(BaseModel):
    """任务创建请求"""
    script_id: Optional[int] = None
    script_ids: List[int] = []
    device_id: Optional[str] = None
    device_ids: List[str] = []
    remark: Optional[str] = None
    apk_id: Optional[int] = None
    project_id: Optional[int] = None
    max_steps: Optional[int] = Field(default=10, ge=1, le=50)
    name: Optional[str] = None
    system_os: Optional[str] = "Android"
    test_type: Optional[str] = "normal"
    compatibility_dimensions: List[str] = []


class ConfigSet(BaseModel):
    """配置设置请求"""
    key: str
    value: str
    description: str = ""
    category: str = "general"


class AgentInit(BaseModel):
    """Agent 初始化请求"""
    protocol: str = Field(..., pattern="^(openapi|anthropic)$")
    base_url: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    apikey: str = Field(..., min_length=1)
    max_steps: int = 10


class DeviceConnect(BaseModel):
    """设备连接请求"""
    ip: str = Field(..., min_length=1)
    port: str = "5555"


class MCPExecute(BaseModel):
    """MCP 工具执行请求"""
    tool_name: str = Field(..., min_length=1)
    parameters: Dict[str, Any] = {}


class BatchDelete(BaseModel):
    """批量删除请求"""
    ids: List[int] = Field(..., min_length=1)


class PreviewFromAgent(BaseModel):
    """Agent 脚本预览请求"""
    task_text: str = Field(..., min_length=1)
    step_results: List[dict] = Field(..., min_length=1)
    device_type: str = "adb"
    system_os: str = "Android"
    page_changes: Optional[List[dict]] = None  # 页面变化记录（用于智能插入截图埋点）


class SaveFromPreview(BaseModel):
    """从预览保存脚本请求"""
    name: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    system_os: str = "Android"
    project_id: Optional[int] = None


class SettingsRequest(BaseModel):
    """设置请求"""
    llm: Optional[Dict[str, Any]] = None
    vlm: Optional[Dict[str, Any]] = None
    adb: Optional[Dict[str, Any]] = None
    system: Optional[Dict[str, Any]] = None
    feishu: Optional[Dict[str, Any]] = None
    agent: Optional[Dict[str, Any]] = None


class ApkMergeRequest(BaseModel):
    """APK 分片合并请求"""
    upload_id: str = Field(..., min_length=1)
    file_hash: str = Field(..., min_length=64, max_length=64)
    file_name: str = Field(..., min_length=1)
    name: Optional[str] = None
    version: Optional[str] = None
    remark: Optional[str] = None


class CompatTaskCreate(BaseModel):
    """兼容性测试任务创建请求"""
    script_id: int = Field(..., ge=1)
    device_ids: List[str] = Field(..., min_length=1)
    platform: str = "Android"
    remark: str = ""
    project_id: Optional[int] = None


class ReflectRequest(BaseModel):
    """反思请求"""
    step_result: dict
    context: dict = {}


class PredictRequest(BaseModel):
    """失败预测请求"""
    step_description: str
    context: dict = {}


class VLMAnalyzeRequest(BaseModel):
    """VLM UI 分析请求"""
    screenshot_base64: str
    context: dict = {}
    dimensions: Optional[List[str]] = None