#!/bin/bash
set -e

echo "[entrypoint] 启动 ADB server..."
adb start-server

echo "[entrypoint] 扫描发现设备..."
python docker_scan_devices.py

echo "[entrypoint] 启动 Web UI..."
exec python web_ui/main.py
