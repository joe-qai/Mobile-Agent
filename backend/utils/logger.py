"""Logging configuration for the application."""

import logging
import os
import sys


def _get_log_level() -> int:
    level_name = os.environ.get("BACKEND_LOG_LEVEL", "INFO").upper()
    return getattr(logging, level_name, logging.INFO)


class RoutineScreenLogFilter(logging.Filter):
    """Suppress routine realtime-screen logs while keeping warnings and errors."""

    ROUTINE_SCREEN_SNIPPETS = (
        "Keyframe packet received",
        "Sending screen packet",
        "Configuration packet received",
        "Data packet received",
        "Reset stream state",
        "Checking device",
        "Device ",
        "Cleaning up existing scrcpy processes",
        "Removing ADB port forward",
        "Waiting for port",
        "Port ",
        "Pushing server to device",
        "Setting up port forwarding",
        "Starting scrcpy server",
        "Connecting to TCP socket",
        "Successfully connected",
        "Scrcpy server started successfully",
        "Streamer stopped",
        "Connection closed or cancelled",
        "Socket closed during shutdown",
        "Cleaning up device screen websocket",
        "Stream ended",
        "Stream task cancelled",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True

        message = record.getMessage()
        return not any(snippet in message for snippet in self.ROUTINE_SCREEN_SNIPPETS)


DEFAULT_LOG_LEVEL = _get_log_level()

# Agent logger 配置：通过 WebSocket 实时推送到前端
# 默认禁用控制台输出，降低开销（Agent 日志已在 Web UI 实时显示）
ENABLE_CONSOLE_LOG = os.environ.get("ENABLE_BACKEND_CONSOLE_LOG", "false").lower() == "true"

logger = logging.getLogger("backend")
logger.setLevel(DEFAULT_LOG_LEVEL)

# 移除已有的 handlers，避免重复
logger.handlers.clear()

# 只在明确启用时才添加控制台输出
if ENABLE_CONSOLE_LOG:
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(DEFAULT_LOG_LEVEL)
    stream_handler.addFilter(RoutineScreenLogFilter())
    logger.addHandler(stream_handler)
else:
    # 添加一个 NullHandler 来阻止 "No handler found" 警告
    logger.addHandler(logging.NullHandler())
