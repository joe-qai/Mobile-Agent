"""
配置管理路由模块
提供系统配置相关的 API 端点
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from web_ui.models.schemas import ConfigSet

router = APIRouter(prefix="/api/configs", tags=["configs"])


@router.get("/")
async def api_get_configs(category: Optional[str] = None):
    """获取所有配置"""
    from backend.db.database import get_configs
    return get_configs(category)


@router.get("/{key}")
async def api_get_config(key: str):
    """获取单个配置"""
    from backend.db.database import get_config
    value = get_config(key)
    if value is None:
        raise HTTPException(status_code=404, detail="Config not found")
    return {"key": key, "value": value}


@router.post("/")
async def api_set_config(data: ConfigSet):
    """设置配置"""
    from backend.db.database import set_config
    set_config(data.key, data.value, data.description, data.category)
    return {"success": True}


@router.delete("/{key}")
async def api_delete_config(key: str):
    """删除配置"""
    from backend.db.database import delete_config
    success = delete_config(key)
    if not success:
        raise HTTPException(status_code=404, detail="Config not found")
    return {"success": True}