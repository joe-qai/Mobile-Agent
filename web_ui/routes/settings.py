"""
设置页面路由模块
提供系统设置相关的 API 端点
"""
import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request

from web_ui.models.schemas import AgentInit

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _test_model_connection(protocol: str, base_url: str, api_key: str, model: str, label: str):
    from backend.llm import llm_protocols

    client = llm_protocols.create_llm_client(protocol, base_url, api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Hi"}],
        max_tokens=5,
    )

    if response.choices and response.choices[0].message.content:
        return {"success": True, "message": f"{label}连接成功"}
    return {"success": False, "message": f"{label}返回空响应"}


@router.get("/")
async def api_get_settings():
    """获取所有设置"""
    from backend.db.database import get_configs_dict
    
    config_keys = [
        "llm_protocol", "llm_base_url", "llm_apikey", "llm_model",
        "adb_path", "adb_timeout", "adb_port",
        "system_workdir", "system_loglevel", "system_logdays", "system_saveinterval",
        "feishu_webhook", "feishu_enabled", "feishu_template",
        "vlm_protocol", "vlm_base_url", "vlm_apikey", "vlm_model"
    ]
    
    config_defaults = {
        "llm_protocol": "openapi",
        "llm_base_url": "",
        "llm_apikey": "",
        "llm_model": "",
        "adb_path": "adb",
        "adb_timeout": "30",
        "adb_port": "5037",
        "system_workdir": "data",
        "system_loglevel": "INFO",
        "system_logdays": "30",
        "system_saveinterval": "60",
        "feishu_webhook": "",
        "feishu_enabled": "false",
        "feishu_template": "<strong>任务完成通知</strong><br>任务名称: {{name}}<br>状态: {{status}}<br>时间: {{time}}",
        "vlm_protocol": "openapi",
        "vlm_base_url": "",
        "vlm_apikey": "",
        "vlm_model": ""
    }
    
    configs = get_configs_dict(config_keys, config_defaults)

    def mask_secret(value: str) -> str:
        """掩码敏感字段：只保留前4位 + ****，空值返回空字符串"""
        if not value:
            return ""
        if len(value) <= 4:
            return "****"
        return value[:4] + "****"

    return {
        "llm": {
            "protocol": configs["llm_protocol"],
            "url": configs["llm_base_url"],
            "apikey": mask_secret(configs["llm_apikey"]),
            "model": configs["llm_model"],
        },
        "adb": {
            "path": configs["adb_path"],
            "timeout": int(configs["adb_timeout"]),
            "port": int(configs["adb_port"]),
        },
        "system": {
            "workdir": configs["system_workdir"],
            "loglevel": configs["system_loglevel"],
            "logdays": int(configs["system_logdays"]),
            "saveinterval": int(configs["system_saveinterval"]),
        },
        "feishu": {
            "webhook": mask_secret(configs["feishu_webhook"]),
            "enabled": configs["feishu_enabled"] == "true",
            "template": configs["feishu_template"],
        },
        "vlm": {
            "protocol": configs["vlm_protocol"],
            "url": configs["vlm_base_url"],
            "apikey": mask_secret(configs["vlm_apikey"]),
            "model": configs["vlm_model"],
        },
    }


@router.post("/")
async def api_save_settings(request: Request):
    """保存设置"""
    from backend.db.database import set_config

    try:
        data = await request.json()
    except Exception:
        return {"success": False, "error": "无效的请求数据"}

    success = True
    if "llm" in data and data["llm"]:
        llm = data["llm"]
        if "protocol" in llm:
            set_config("llm_protocol", llm["protocol"], "LLM协议类型", "llm")
        llm_url = llm.get("base_url") or llm.get("url", "")
        if llm_url:
            set_config("llm_base_url", llm_url, "LLM API地址", "llm")
        if "apikey" in llm and not _is_masked(llm["apikey"]):
            set_config("llm_apikey", llm["apikey"], "LLM API密钥", "llm")
        if "model" in llm:
            set_config("llm_model", llm["model"], "LLM模型", "llm")

    if "vlm" in data and data["vlm"]:
        vlm = data["vlm"]
        if "protocol" in vlm:
            set_config("vlm_protocol", vlm["protocol"], "VLM协议类型", "vlm")
        vlm_url = vlm.get("base_url") or vlm.get("url", "")
        if vlm_url:
            set_config("vlm_base_url", vlm_url, "VLM API地址", "vlm")
        if "apikey" in vlm and not _is_masked(vlm["apikey"]):
            set_config("vlm_apikey", vlm["apikey"], "VLM API密钥", "vlm")
        if "model" in vlm:
            set_config("vlm_model", vlm["model"], "VLM模型", "vlm")

    if "adb" in data and data["adb"]:
        adb = data["adb"]
        if "path" in adb:
            set_config("adb_path", str(adb["path"]), "ADB路径", "adb")
        if "timeout" in adb:
            set_config("adb_timeout", str(adb["timeout"]), "ADB超时时间", "adb")
        if "port" in adb:
            set_config("adb_port", str(adb["port"]), "ADB端口", "adb")

    if "system" in data and data["system"]:
        sys = data["system"]
        if "workdir" in sys:
            set_config("system_workdir", str(sys["workdir"]), "系统工作目录", "system")
        if "loglevel" in sys:
            set_config("system_loglevel", str(sys["loglevel"]), "日志级别", "system")
        if "logdays" in sys:
            set_config("system_logdays", str(sys["logdays"]), "日志保留天数", "system")
        if "saveinterval" in sys:
            set_config("system_saveinterval", str(sys["saveinterval"]), "自动保存间隔", "system")

    if "feishu" in data and data["feishu"]:
        feishu = data["feishu"]
        if "webhook" in feishu and not _is_masked(feishu["webhook"]):
            set_config("feishu_webhook", feishu["webhook"], "飞书Webhook地址", "feishu")
        if "enabled" in feishu:
            set_config("feishu_enabled", str(feishu["enabled"]).lower(), "飞书通知开关", "feishu")
        if "template" in feishu:
            set_config("feishu_template", feishu["template"], "飞书通知模板", "feishu")

    return {"success": success}


def _is_masked(value: str) -> bool:
    """判断值是否是脱敏后的掩码值"""
    return value and "****" in value


def _get_real_secret(key: str) -> str:
    """从数据库获取真实的敏感配置值"""
    from backend.db.database import get_config
    return get_config(key) or ""


@router.post("/test/llm")
async def api_test_llm_connection(request: Request):
    """测试LLM连接"""
    try:
        data = await request.json()
        protocol = data.get("protocol", "openapi")
        base_url = data.get("base_url") or data.get("url", "")
        api_key = data.get("apikey", "")
        model = data.get("model", "")

        if _is_masked(api_key):
            api_key = _get_real_secret("llm_apikey")

        if not base_url:
            return {"success": False, "message": "请输入 API 地址"}

        return await asyncio.to_thread(
            _test_model_connection,
            protocol,
            base_url,
            api_key,
            model,
            "LLM",
        )
    except Exception as e:
        return {"success": False, "message": f"测试失败: {str(e)}"}


@router.post("/test/vlm")
async def api_test_vlm_connection(request: Request):
    """测试VLM连接"""
    try:
        data = await request.json()
        protocol = data.get("protocol", "openapi")
        base_url = data.get("base_url") or data.get("url", "")
        api_key = data.get("apikey", "")
        model = data.get("model", "")

        if _is_masked(api_key):
            api_key = _get_real_secret("vlm_apikey")

        if not base_url:
            return {"success": False, "message": "请输入 API 地址"}

        return await asyncio.to_thread(
            _test_model_connection,
            protocol,
            base_url,
            api_key,
            model,
            "VLM",
        )
    except Exception as e:
        return {"success": False, "message": f"测试失败: {str(e)}"}


@router.get("/test/adb")
async def api_test_adb_connection():
    """测试ADB连接"""
    try:
        from backend.mcp.mcp_tools import mcp_tools
        devices = await asyncio.to_thread(mcp_tools.discover_devices)
        if devices:
            return {"success": True, "message": f"ADB连接成功，发现{len(devices)}个设备"}
        else:
            return {"success": False, "message": "未发现已连接的设备"}
    except Exception as e:
        return {"success": False, "message": f"ADB连接测试失败: {str(e)}"}


@router.post("/test/feishu")
async def api_test_feishu_notification(request: Request):
    """测试飞书通知"""
    try:
        from backend.notification.notifiers.feishu import FeishuNotifier
        notifier = FeishuNotifier()
        success = await notifier.test_connection()
        return {"success": success, "message": f"飞书通道测试{'成功' if success else '失败'}"}
    except Exception as e:
        return {"success": False, "message": f"飞书通知测试失败: {str(e)}"}


@router.post("/test/notification")
async def api_test_notification_channel(request: Request):
    """测试指定通知通道"""
    try:
        from backend.notification.notifiers import get_notifier
        data = await request.json()
        channel = data.get("channel", "feishu")
        notifier = get_notifier(channel)
        if notifier is None:
            return {"success": False, "message": f"通道 {channel} 未注册"}
        success = await notifier.test_connection()
        return {"success": success, "message": f"{channel} 通道测试{'成功' if success else '失败'}"}
    except Exception as e:
        return {"success": False, "message": f"测试失败: {str(e)}"}


# --- Notification Rule APIs ---
@router.get("/notification/rules")
async def api_get_notification_rules():
    """获取通知规则列表"""
    import json as _json

    from backend.db.database import get_notification_rules
    rules = get_notification_rules()
    for rule in rules:
        rule["conditions"] = _json.loads(rule["conditions"]) if rule["conditions"] else {}
        rule["channels"] = _json.loads(rule["channels"]) if rule["channels"] else []
        rule["enabled"] = rule["enabled"] == "true"
    return {"rules": rules}


@router.post("/notification/rules")
async def api_create_notification_rule(request: Request):
    """创建通知规则"""
    import json as _json

    from backend.db.database import create_notification_rule
    data = await request.json()
    rule_id = create_notification_rule(
        name=data.get("name", ""),
        event_type=data.get("event_type", ""),
        conditions=_json.dumps(data.get("conditions", {})),
        channels=_json.dumps(data.get("channels", ["feishu"])),
        enabled=data.get("enabled", True),
        priority=data.get("priority", 10),
    )
    return {"success": True, "id": rule_id}


@router.put("/notification/rules/{rule_id}")
async def api_update_notification_rule(rule_id: int, request: Request):
    """更新通知规则"""
    import json as _json

    from backend.db.database import update_notification_rule
    data = await request.json()
    success = update_notification_rule(
        rule_id,
        name=data.get("name"),
        event_type=data.get("event_type"),
        conditions=_json.dumps(data.get("conditions")) if "conditions" in data else None,
        channels=_json.dumps(data.get("channels")) if "channels" in data else None,
        enabled=data.get("enabled"),
        priority=data.get("priority"),
    )
    return {"success": success}


@router.delete("/notification/rules/{rule_id}")
async def api_delete_notification_rule(rule_id: int):
    """删除通知规则"""
    from backend.db.database import delete_notification_rule
    success = delete_notification_rule(rule_id)
    return {"success": success}


@router.get("/notification/logs")
async def api_get_notification_logs(limit: int = 50):
    """获取通知日志"""
    from backend.db.database import get_notification_logs
    logs = get_notification_logs(limit=limit)
    return {"logs": logs}


# 独立 APIRouter，用于无前缀的 API（/api/init_agent, /api/health）
init_agent_router = APIRouter(tags=["settings"])


@init_agent_router.get("/api/health")
async def api_health():
    """健康检查"""
    from datetime import datetime
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@init_agent_router.post("/api/init_agent")
async def api_init_agent(data: AgentInit):
    """初始化Agent"""
    from backend.agent.agent_manager import agent_manager
    from backend.db.database import set_config
    from backend.llm.llm_protocols import init_llm
    
    success = init_llm(data.protocol, data.base_url, data.apikey, data.model)
    if success:
        agent_manager.init_agent()
        set_config("llm_protocol", data.protocol, "LLM协议类型", "llm")
        set_config("llm_base_url", data.base_url, "LLM基础URL", "llm")
        set_config("llm_model", data.model, "LLM模型名称", "llm")
        set_config("llm_max_steps", str(data.max_steps), "最大执行步骤", "llm")
    return {"success": success}
