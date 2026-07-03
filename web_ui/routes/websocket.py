"""
WebSocket 路由模块
提供实时通信相关的 WebSocket 端点
"""
import asyncio
import json
import logging
from typing import Dict, List

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("backend")

from web_ui.utils.logger import LOG_MESSAGE_CACHE, connected_clients

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    """实时日志推送"""
    await websocket.accept()
    connected_clients.append(websocket)

    for msg in LOG_MESSAGE_CACHE:
        try:
            await websocket.send_text(msg)
        except Exception:
            break

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        if websocket in connected_clients:
            connected_clients.remove(websocket)
    except Exception:
        if websocket in connected_clients:
            connected_clients.remove(websocket)


@router.websocket("/ws/compatibility/{parent_task_id}")
async def websocket_compatibility(websocket: WebSocket, parent_task_id: int):
    """兼容性测试实时事件订阅"""
    await websocket.accept()
    
    # 定义回调函数
    async def compat_event_callback(event_type: str, data: dict):
        try:
            await websocket.send_json({
                "type": event_type,
                "data": data
            })
        except Exception:
            pass
    
    # 注册回调
    try:
        from backend.compatibility.compatibility_service import compatibility_service
        compatibility_service.register_websocket_callback(parent_task_id, compat_event_callback)
    except ImportError:
        pass
    
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        try:
            from backend.compatibility.compatibility_service import (
                compatibility_service,
            )
            compatibility_service.unregister_websocket_callback(compat_event_callback)
        except ImportError:
            pass


@router.websocket("/ws/screen")
async def websocket_screen(websocket: WebSocket):
    """屏幕画面实时传输 - 在线程池中执行截图避免阻塞"""
    await websocket.accept()
    from backend.mcp.mcp_tools import mcp_tools
    
    try:
        while True:
            await websocket.receive_text()
            if mcp_tools.current_device:
                result = await asyncio.to_thread(mcp_tools.get_screen_image)
                await websocket.send_json(result)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


# Device streamer management
device_streamers: dict = {}
device_stream_locks: dict = {}

# Progress tracking
progress_clients: Dict[int, List[WebSocket]] = {}
progress_locks: Dict[int, asyncio.Lock] = {}


@router.websocket("/ws/progress/{task_id}")
async def websocket_progress(websocket: WebSocket, task_id: int):
    """任务进度实时推送"""
    await websocket.accept()

    if task_id not in progress_locks:
        progress_locks[task_id] = asyncio.Lock()

    async with progress_locks[task_id]:
        if task_id not in progress_clients:
            progress_clients[task_id] = []
        progress_clients[task_id].append(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        async with progress_locks[task_id]:
            if task_id in progress_clients and websocket in progress_clients[task_id]:
                progress_clients[task_id].remove(websocket)
                if not progress_clients[task_id]:
                    del progress_clients[task_id]
    except Exception:
        async with progress_locks[task_id]:
            if task_id in progress_clients and websocket in progress_clients[task_id]:
                progress_clients[task_id].remove(websocket)
                if not progress_clients[task_id]:
                    del progress_clients[task_id]


def _classify_scrcpy_error(exc: Exception) -> dict:
    error_str = str(exc)
    if "Address already in use" in error_str or (
        "Port" in error_str and "occupied" in error_str
    ):
        return {"message": "端口冲突，视频流端口仍被占用", "type": "port_conflict"}
    elif "Device" in error_str and (
        "not available" in error_str or "not found" in error_str
    ):
        return {"message": "设备无响应，请检查 USB/WiFi 连接", "type": "device_offline"}
    elif "timeout" in error_str.lower() or "timed out" in error_str.lower():
        return {"message": "连接超时，请重试", "type": "timeout"}
    elif "Segmentation fault" in error_str or "exit code 139" in error_str:
        return {"message": "视频流服务启动失败（设备不兼容），降级为截图模式", "type": "start_failed"}
    elif "Socket closed by remote" in error_str:
        return {"message": "设备不兼容视频流服务（Android 11+ 需），降级为截图模式", "type": "start_failed"}
    else:
        return {"message": error_str, "type": "unknown"}


@router.websocket("/ws/device-screen")
async def websocket_device_screen(websocket: WebSocket):
    """Scrcpy real-time screen streaming via WebSocket"""
    await websocket.accept()
    from starlette.websockets import WebSocketState

    from backend.scrcpy.scrcpy_streamer import ScrcpyStreamer
    
    streamer = None
    device_id = None

    try:
        message = await websocket.receive()
        
        if "text" in message:
            try:
                data = json.loads(message["text"])
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"error": {"message": "Invalid JSON format", "type": "invalid_params"}}
                )
                return
        elif "bytes" in message:
            await websocket.send_json(
                {"error": {"message": "Expected JSON device_id, received binary", "type": "invalid_params"}}
            )
            return
        else:
            await websocket.send_json(
                {"error": {"message": "Unknown message format", "type": "invalid_params"}}
            )
            return
            
        device_id = data.get("device_id")
        if not device_id:
            await websocket.send_json(
                {"error": {"message": "device_id required", "type": "invalid_params"}}
            )
            return

        if device_id not in device_stream_locks:
            device_stream_locks[device_id] = asyncio.Lock()

        async with device_stream_locks[device_id]:
            streamer = None
            disconnected = False

            async def _safe_send_json(payload):
                nonlocal disconnected
                if websocket.client_state != WebSocketState.CONNECTED:
                    disconnected = True
                    return False
                try:
                    await websocket.send_json(payload)
                    return True
                except (WebSocketDisconnect, RuntimeError):
                    disconnected = True
                    return False

            async def _safe_send_bytes(payload):
                nonlocal disconnected
                if websocket.client_state != WebSocketState.CONNECTED:
                    disconnected = True
                    return False
                try:
                    await websocket.send_bytes(payload)
                    return True
                except (WebSocketDisconnect, RuntimeError):
                    disconnected = True
                    return False

            async def _ensure_stream():
                nonlocal streamer
                if streamer:
                    streamer.stop()
                s = ScrcpyStreamer(
                    device_id=device_id, max_size=1080, bit_rate=6_000_000, max_fps=60
                )
                meta = await s.start()
                device_streamers[device_id] = s
                streamer = s
                return meta

            try:
                metadata = await _ensure_stream()

                if not await _safe_send_json(
                    {
                        "deviceName": metadata.device_name or "",
                        "width": metadata.width or 0,
                        "height": metadata.height or 0,
                    }
                ):
                    return

                while True:
                    async for packet in streamer.iter_packets():
                        if disconnected:
                            return
                        clean_pts = (packet.pts or 0) & 0x3FFFFFFFFFFFFFFF

                        if packet.type == "configuration":
                            frame_type = 0
                        elif packet.keyframe:
                            frame_type = 1
                        else:
                            frame_type = 2

                        header = bytearray(13)
                        header[0] = frame_type
                        for i in range(8):
                            header[1 + i] = (clean_pts >> ((7 - i) * 8)) & 0xFF
                        data_len = len(packet.data)
                        for i in range(4):
                            header[9 + i] = (data_len >> ((3 - i) * 8)) & 0xFF

                        frame = bytes(header) + packet.data

                        if not await _safe_send_bytes(frame):
                            break
            except Exception as e:
                error_info = _classify_scrcpy_error(e)
                logger.info(f"scrcpy failed (type={error_info['type']}), switching to screenshot fallback for {device_id}: {error_info['message']}")
                if not await _safe_send_json({"fallback": True, "reason": error_info["message"], "type": error_info["type"]}):
                    return

                from backend.mcp.mcp_tools import mcp_tools
                fallback_width = 0
                fallback_height = 0
                poll_count = 0
                while not disconnected:
                    try:
                        result = await asyncio.to_thread(mcp_tools.get_screen_image, device_id)
                        if result.get("image_base64"):
                            poll_count += 1
                            if poll_count == 1:
                                logger.info(f"screenshot fallback first frame received for {device_id}")
                            fallback_width = result.get("width", fallback_width)
                            fallback_height = result.get("height", fallback_height)
                            if not await _safe_send_json({
                                "is_fallback": True,
                                "image_base64": result["image_base64"],
                                "width": fallback_width,
                                "height": fallback_height,
                            }):
                                break
                        else:
                            logger.warning(f"screenshot fallback empty result for {device_id}: {result.get('error', 'no image_base64')}")
                    except Exception as ex:
                        logger.error(f"screenshot fallback error for {device_id}: {ex}")
                    await asyncio.sleep(0.5)
    finally:
        if streamer:
            try:
                streamer.stop()
            except Exception:
                pass
        if device_id and device_id in device_streamers:
            try:
                del device_streamers[device_id]
            except Exception:
                pass
