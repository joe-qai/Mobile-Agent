"""
日志处理工具模块
提供日志队列、WebSocket 日志处理等功能
"""
import asyncio
import json
import logging
import queue
import re
from collections import deque
from typing import List

connected_clients: List = []

LOG_MESSAGE_CACHE: List[str] = []
MAX_CACHE_SIZE = 100

LOG_QUEUE = queue.Queue()

_RECENT_REALTIME_LOG_MESSAGES = deque(maxlen=80)

IGNORED_WS_LOGGER_PREFIXES = (
    "httpx",
    "httpcore",
    "uvicorn.access",
    "agent",
    "backend.compatibility",
)

IGNORED_WS_LOG_MESSAGE_SNIPPETS = (
    "HTTP Request:",
    "Keyframe packet received",
    "Sending screen packet",
    "Configuration packet received",
    "Data packet received",
    "device-screen",
    "screen websocket",
)

IGNORED_REALTIME_LOG_MESSAGE_SNIPPETS = IGNORED_WS_LOG_MESSAGE_SNIPPETS + (
    "uiautomator dump",
    "dumpsys 输出",
    "bash arg:",
    "Events injected:",
    "Network stats",
    "No such file or directory",
    "UI XML",
    "Started server process",
    "Waiting for application startup",
    "日志队列处理任务已启动",
    "Application startup complete",
    "connection open",
    "Reset stream state",
    "Checking device",
    "Removing ADB port forward",
    "Waiting for port",
    "available for binding",
    "became available",
    "successfully released",
    "Pushing server to device",
    "Setting up port forwarding",
    "Server process poll",
    "Connecting to TCP socket",
    "Successfully connected",
    " is available",
    "[兼容性测试]",
)

IGNORED_REALTIME_LOG_MESSAGE_PATTERNS = (
    r"^应用名称 '.+' 映射到包名:",
    r"^尝试使用 '.+' 启动应用",
    r"^'.+' 输出:",
    r"^应用 '.+' 启动成功 \(使用 .+\)",
    r"^未能获取有效的UI XML",
    r"^获取到的内容不是有效的XML:",
)

AGENT_RELATED_LOG_PATTERNS = (
    r"\[ReActAgent\]",
    r"\[启动\]",
    r"\[任务分析\]",
    r"\[Step \d+\]",
    r"\[思考\]",
    r"\[工具执行\]",
    r"\[感知\]",
    r"\[结束\]",
    r"\[降级\]",
    r"\[ReAct模式\]",
    r"\[主要方案\]",
    r"\[降级方案\]",
    r"\[降级成功\]",
    r"\[降级失败\]",
    r"\[任务失败\]",
    r"click_element",
    r"launch_app",
    r"input_text",
    r"swipe_screen",
    r"UI tree",
    r"VLM 降级",
    r"writing-skills",
    r"executing-plans",
    r"点击追踪",
)

BASE64_LOG_PAYLOAD_PATTERN = re.compile(r"^[A-Za-z0-9+/=\s]{512,}$")
BASE64_RESPONSE_FIELD_PATTERN = re.compile(
    r'"(?:image_base64|screenshot_base64|screenshot|base64_data)"\s*:',
    re.IGNORECASE,
)


def strip_ansi_codes(text: str) -> str:
    ansi_pattern = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_pattern.sub("", text)


def _normalize_realtime_log_message(message: str) -> str:
    text = str(message or "").strip()
    text = re.sub(r"^\d{2}:\d{2}:\d{2}\s+\[[A-Z]+\]\s+", "", text)
    return text


def should_skip_realtime_log_message(message: str) -> bool:
    text = _normalize_realtime_log_message(message)
    if not text:
        return True

    if any(re.search(pattern, text) for pattern in AGENT_RELATED_LOG_PATTERNS):
        return False

    if '"type": "screenshot_preview"' in text or '"type":"screenshot_preview"' in text:
        return False

    compact_text = re.sub(r"\s+", "", text)
    if (
        text.startswith("data:image/")
        or BASE64_LOG_PAYLOAD_PATTERN.fullmatch(text)
        or BASE64_RESPONSE_FIELD_PATTERN.search(text)
        or BASE64_RESPONSE_FIELD_PATTERN.search(compact_text)
    ):
        return True

    if any(snippet in text for snippet in IGNORED_REALTIME_LOG_MESSAGE_SNIPPETS):
        return True

    return any(
        re.search(pattern, text, re.IGNORECASE)
        for pattern in IGNORED_REALTIME_LOG_MESSAGE_PATTERNS
    )


def should_skip_websocket_log_record(record: logging.LogRecord) -> bool:
    logger_name = record.name or ""
    if any(
        logger_name == prefix or logger_name.startswith(f"{prefix}.")
        for prefix in IGNORED_WS_LOGGER_PREFIXES
    ):
        return True

    return should_skip_realtime_log_message(record.getMessage())


async def process_log_queue():
    while True:
        try:
            msg = LOG_QUEUE.get(timeout=1)
            LOG_MESSAGE_CACHE.append(msg)
            if len(LOG_MESSAGE_CACHE) > MAX_CACHE_SIZE:
                LOG_MESSAGE_CACHE.pop(0)

            # Forward progress messages to progress_clients
            if '"type": "progress"' in msg or '"type":"progress"' in msg:
                try:
                    from web_ui.routes.websocket import progress_clients
                    parsed = json.loads(msg)
                    payload = json.dumps(parsed.get("payload", parsed))
                    dead_tasks = []
                    for task_id, clients in progress_clients.items():
                        for ws in clients[:]:
                            try:
                                await ws.send_text(payload)
                            except Exception:
                                try:
                                    clients.remove(ws)
                                except ValueError:
                                    pass
                        if not clients:
                            dead_tasks.append(task_id)
                    for tid in dead_tasks:
                        progress_clients.pop(tid, None)
                except Exception:
                    pass

            for client in connected_clients[:]:
                try:
                    await client.send_text(msg)
                except Exception:
                    try:
                        connected_clients.remove(client)
                    except ValueError:
                        pass
            LOG_QUEUE.task_done()
        except queue.Empty:
            await asyncio.sleep(0.01)


def enqueue_log(message: str) -> None:
    if should_skip_realtime_log_message(message):
        return

    normalized_message = _normalize_realtime_log_message(message)
    is_screenshot_preview = '"type": "screenshot_preview"' in normalized_message
    if (
        not is_screenshot_preview
        and normalized_message in _RECENT_REALTIME_LOG_MESSAGES
    ):
        return

    if not is_screenshot_preview:
        _RECENT_REALTIME_LOG_MESSAGES.append(normalized_message)
    LOG_QUEUE.put(message)


def log_callback(message: str) -> None:
    message = strip_ansi_codes(message)
    enqueue_log(message)


class WebSocketLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))

    def emit(self, record: logging.LogRecord):
        if should_skip_websocket_log_record(record):
            return
        msg = self.format(record)
        msg = strip_ansi_codes(msg)
        enqueue_log(msg)

    def flush(self):
        pass