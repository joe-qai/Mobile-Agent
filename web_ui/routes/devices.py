"""设备管理路由"""
import asyncio
import logging
import time
from typing import Optional

from fastapi import APIRouter, Body, HTTPException

router = APIRouter(prefix="/api/devices", tags=["devices"])

from backend.db.database import format_device_display_name, sync_discovered_devices
from backend.mcp.mcp_tools import mcp_tools
from web_ui.models.schemas import DeviceConnect

logger = logging.getLogger(__name__)

_DEVICES_CACHE_TTL = 2.0
_devices_cache = {"time": 0.0, "devices": None}


async def discover_devices_cached(force_refresh: bool = False):
    """Discover devices off the event loop with a short TTL cache."""
    now = time.monotonic()
    cached_devices = _devices_cache.get("devices")
    if (
        not force_refresh
        and cached_devices is not None
        and now - float(_devices_cache.get("time", 0.0)) < _DEVICES_CACHE_TTL
    ):
        return cached_devices

    devices = await asyncio.to_thread(mcp_tools.discover_devices)
    try:
        await asyncio.to_thread(sync_discovered_devices, devices)
    except Exception:
        logger.exception("sync_discovered_devices failed; continuing with cache write")
    _devices_cache["devices"] = devices
    _devices_cache["time"] = time.monotonic()
    return devices


def invalidate_devices_cache():
    _devices_cache["devices"] = None
    _devices_cache["time"] = 0.0


def device_to_response_dict(device) -> dict:
    payload = device.to_dict() if hasattr(device, "to_dict") else dict(device)
    payload["device_display_name"] = format_device_display_name(
        payload.get("brand"),
        payload.get("model"),
    )
    return payload


@router.get("/")
async def api_get_devices(system_os: Optional[str] = None):
    """获取设备列表（支持系统过滤）"""
    devices = await discover_devices_cached()
    if system_os:
        devices = [d for d in devices if getattr(d, 'system_os', 'Android') == system_os]
    return {
        "devices": [device_to_response_dict(d) for d in devices],
        "current_device": mcp_tools.current_device,
    }


@router.post("/connect")
async def api_connect_device(data: DeviceConnect):
    """连接WiFi设备"""
    result = await asyncio.to_thread(mcp_tools.connect_wireless_device, data.ip, data.port)
    invalidate_devices_cache()
    return result


@router.post("/disconnect")
async def api_disconnect_device(device_id: str = Body(..., embed=True)):
    """断开设备连接"""
    result = await asyncio.to_thread(mcp_tools.disconnect_device, device_id)
    invalidate_devices_cache()
    return result


@router.post("/select")
async def api_select_device(device_id: str = Body(..., embed=True)):
    """选择当前设备"""
    try:
        await asyncio.to_thread(mcp_tools.select_device, device_id)
        return {"success": True, "message": "设备选择成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{device_id}/select")
async def api_select_device_by_id(device_id: str):
    """选择当前设备（路径参数格式）"""
    try:
        await asyncio.to_thread(mcp_tools.select_device, device_id)
        return {"success": True, "message": "设备选择成功"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/screen/capture")
async def api_capture_screen(device_id: Optional[str] = Body(None, embed=True)):
    """捕获设备屏幕"""
    try:
        result = await asyncio.to_thread(mcp_tools.capture_screen, device_id)
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/input/text")
async def api_input_text(text: str = Body(..., embed=True), device_id: Optional[str] = Body(None, embed=True)):
    """在设备上输入文本"""
    try:
        result = await asyncio.to_thread(mcp_tools.input_text, text, device_id)
        return {"success": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tap")
async def api_tap(x: int = Body(..., embed=True), y: int = Body(..., embed=True), device_id: Optional[str] = Body(None, embed=True)):
    """点击设备屏幕"""
    try:
        return await asyncio.to_thread(mcp_tools.click, x, y, device_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/swipe")
async def api_swipe(start_x: int = Body(..., embed=True), start_y: int = Body(..., embed=True), end_x: int = Body(0, embed=True), end_y: int = Body(..., embed=True), device_id: Optional[str] = Body(None, embed=True)):
    """在设备上滑动"""
    try:
        return await asyncio.to_thread(mcp_tools.swipe, start_x, start_y, end_x, end_y, device_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/keyevent")
async def api_keyevent(keycode: int = Body(..., embed=True), device_id: Optional[str] = Body(None, embed=True)):
    """发送按键事件"""
    try:
        result = await asyncio.to_thread(mcp_tools.keyevent, keycode, device_id)
        return {"success": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/screen")
async def api_get_screen(device_id: Optional[str] = None):
    """获取屏幕截图（scrcpy 播放器使用）"""
    try:
        result = await asyncio.to_thread(mcp_tools.get_screen_image, device_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/screenshot")
async def api_get_screenshot():
    """获取当前设备截图"""
    try:
        screenshot = await asyncio.to_thread(mcp_tools.get_screenshot)
        if screenshot:
            return {"success": True, "data": screenshot}
        else:
            return {"success": False, "message": "无法获取截图"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/install_apk")
async def api_install_apk(apk_path: str = Body(..., embed=True), device_id: Optional[str] = Body(None, embed=True)):
    """安装APK"""
    try:
        result = await asyncio.to_thread(mcp_tools.install_apk, apk_path, device_id)
        return {"success": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/uninstall_app")
async def api_uninstall_app(package_name: str = Body(..., embed=True), device_id: Optional[str] = Body(None, embed=True)):
    """卸载应用"""
    try:
        result = await asyncio.to_thread(mcp_tools.uninstall_app, package_name, device_id)
        return {"success": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/apps")
async def api_get_installed_apps(device_id: Optional[str] = None):
    """获取已安装应用列表"""
    try:
        apps = await asyncio.to_thread(mcp_tools.get_installed_apps, device_id)
        return {"apps": apps}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/start_app")
async def api_start_app(package_name: str = Body(..., embed=True), device_id: Optional[str] = Body(None, embed=True)):
    """启动应用"""
    try:
        result = await asyncio.to_thread(mcp_tools.start_app, package_name, device_id)
        return {"success": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop_app")
async def api_stop_app(package_name: str = Body(..., embed=True), device_id: Optional[str] = Body(None, embed=True)):
    """停止应用"""
    try:
        result = await asyncio.to_thread(mcp_tools.stop_app, package_name, device_id)
        return {"success": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/info")
async def api_get_device_info(device_id: Optional[str] = None):
    """获取设备信息"""
    try:
        info = await asyncio.to_thread(mcp_tools.get_device_info, device_id)
        return {"info": info}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reboot")
async def api_reboot_device(device_id: Optional[str] = Body(None, embed=True)):
    """重启设备"""
    try:
        result = await asyncio.to_thread(mcp_tools.reboot_device, device_id)
        return {"success": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
