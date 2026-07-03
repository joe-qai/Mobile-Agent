"""ReAct Agent 与 AgentManager 集成模块"""

import os
import time
from typing import Any, Callable, Dict, List, Optional

from backend.agent.react_agent import AgentConfig, ReActAgent
from backend.llm.llm_protocols import ReActAgentLLMClient
from backend.mcp.mcp_tools_adb import ADBMCTools
from backend.mcp.mcp_tools_base import MCPToolsBase, ScreenInfo, UIElement, UITreeResult


def create_react_agent(
    llm_protocol,
    device_id: Optional[str] = None,
    log_callback: Optional[Callable[[str], None]] = None,
    max_steps: int = 20,
) -> ReActAgent:
    """创建 ReAct Agent 实例"""
    # 创建 ADB MCP 工具实例（新版）
    mcp_tools = ADBMCTools(device_id=device_id)

    # 设置日志回调
    if log_callback:
        mcp_tools.set_log_callback(log_callback)

    # 创建 LLM 客户端
    tool_definitions = mcp_tools.get_tool_definitions()
    llm_client = ReActAgentLLMClient(
        llm_protocol=llm_protocol, tools=tool_definitions, log_callback=log_callback
    )

    # 创建 Agent 配置
    config = AgentConfig(max_steps=max_steps, verbose=True, stop_on_error=True)

    # 创建 ReAct Agent
    agent = ReActAgent(
        llm_client=llm_client,
        mcp_tools=mcp_tools,
        config=config,
        log_callback=log_callback,
    )

    return agent


def discover_devices() -> List[Dict[str, Any]]:
    """发现可用设备（委托给 ADBMCTools）"""
    tools = ADBMCTools()
    devices = tools.discover_devices()
    return [
        {
            "id": d.id,
            "name": d.name,
            "model": d.model,
            "version": d.version,
            "status": d.status,
            "connection_type": d.connection_type,
            "ip": d.ip,
            "port": d.port,
            "platform": d.platform,
        }
        for d in devices
    ]


def connect_wireless_device(
    ip: str, port: str = "5555", usb_device_id: str = None
) -> Dict[str, Any]:
    """连接 WiFi 设备"""
    tools = ADBMCTools()
    return tools.connect_wireless_device(ip, port, usb_device_id)


def disconnect_device(device_id: str) -> Dict[str, Any]:
    """断开设备连接"""
    tools = ADBMCTools()
    return tools.disconnect_device(device_id)


def get_device_info(device_id: str) -> Optional[Dict[str, Any]]:
    """获取设备信息"""
    tools = ADBMCTools()
    device = tools.get_device_info(device_id)
    if device:
        return {
            "id": device.id,
            "name": device.name,
            "model": device.model,
            "version": device.version,
            "status": device.status,
            "connection_type": device.connection_type,
            "platform": device.platform,
        }
    return None
