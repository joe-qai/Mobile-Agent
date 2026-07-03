"""飞书通知通道 — lark_md 交互式卡片格式"""
import json
import logging
import os
import socket
from urllib.parse import urlparse

import httpx

from backend.db.database import get_config
from backend.notification.base import BaseNotifier, NotificationEvent

logger = logging.getLogger(__name__)


class FeishuNotifier(BaseNotifier):
    name = "feishu"

    def get_default_template(self) -> str:
        return "**Agent - {{task_name}}**\n\n**状态：**{{status}}\n**测试设备：**{{device_info}}\n**测试时间：**{{completed_at}}"

    async def send(self, event: NotificationEvent) -> bool:
        try:
            if get_config("feishu_enabled", "false") != "true":
                return True
            webhook = get_config("feishu_webhook", "")
            if not webhook:
                logger.warning("飞书Webhook未配置")
                return True
            device_info = self._resolve_device_info(event)
            payload = self._build_card_payload(event, device_info)
            async with httpx.AsyncClient() as client:
                resp = await client.post(webhook, json=payload, timeout=10)
                return resp.status_code == 200
        except Exception as e:
            logger.error(f"飞书通知发送异常: {e}")
            return False

    async def test_connection(self) -> bool:
        webhook = get_config("feishu_webhook", "")
        if not webhook:
            return False
        payload = {
            "msg_type": "interactive",
            "card": {
                "config": {"enable_forward": True, "update_multi": False},
                "header": {"template": "blue", "title": {"tag": "plain_text", "content": "【测试】飞书通知连通性测试"}},
                "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "这是一条测试消息，验证飞书Webhook连通性。"}}],
            },
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(webhook, json=payload, timeout=10)
                return resp.status_code == 200
        except Exception as e:
            logger.error(f"飞书连通性测试失败: {e}")
            return False

    def _severity_to_color(self, severity: str) -> str:
        """将 severity 映射为飞书卡片 header 颜色"""
        return {
            "blocker": "red",
            "major": "red",
            "minor": "orange",
            "blocking": "red",
            "severe": "red",
            "warning": "orange",
            "suggestion": "green",
        }.get(severity, "red")

    def _detect_lan_ip(self) -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            try:
                return socket.gethostbyname(socket.gethostname())
            except OSError:
                return "127.0.0.1"
        finally:
            sock.close()

    def _server_base_url(self, event: NotificationEvent | None = None) -> str:
        if event and event.extra.get("server_base_url"):
            return str(event.extra["server_base_url"]).rstrip("/")

        configured = (
            get_config("web_ui_base_url", "")
            or os.getenv("WEB_UI_BASE_URL", "")
        )
        if configured:
            return configured.rstrip("/")

        scheme = os.getenv("WEB_UI_SCHEME", "http")
        port = (
            os.getenv("WEB_UI_PORT")
            or get_config("web_ui_port", "8001")
            or "8001"
        )
        return f"{scheme}://{self._detect_lan_ip()}:{port}"

    def _absolute_report_url(self, event: NotificationEvent) -> str:
        report_url = str(event.extra.get("report_url") or "/reports")
        parsed = urlparse(report_url)
        if parsed.scheme and parsed.netloc:
            return report_url
        return f"{self._server_base_url(event)}/{report_url.lstrip('/')}"

    def _resolve_device_info(self, event: NotificationEvent) -> str:
        """从 event 中解析设备信息，转为型号+版本。

        - 兼容性父任务传入的 device_id 可能已是拼好的展示字符串（含空格/中文/括号），直接返回。
        - 单设备序列号格式走 mcp_tools → DB 回退 → "未知设备" 的解析链路，避免暴露序列号。
        """
        if event.device_id:
            raw = str(event.device_id).strip()
            # 已是展示名格式（含空格、中文、括号、" / " 分隔多设备），直接返回
            if " " in raw or "/" in raw or "(" in raw or any(
                "\u4e00" <= ch <= "\u9fff" for ch in raw
            ):
                return raw
            # 单设备 ID：走解析链路
            try:
                from backend.mcp.mcp_tools import mcp_tools
                dev = mcp_tools.get_device_info(raw)
                if dev and getattr(dev, "model", ""):
                    version = getattr(dev, "version", "") or ""
                    brand = getattr(dev, "brand", "") or ""
                    parts = [p for p in (brand, dev.model) if p]
                    if parts:
                        name = " ".join(parts)
                        if version:
                            name += f" (android {version})"
                        return name
            except Exception:
                pass
            # DB 库存回退
            try:
                from backend.db.database import get_device_display_name_from_db
                db_name = get_device_display_name_from_db(raw)
                if db_name:
                    return db_name
            except Exception:
                pass
            # 最终回退：不展示序列号
            return "未知设备"
        try:
            data = json.loads(event.result) if isinstance(event.result, str) else {}
            if data.get("devices"):
                return data["devices"]
            if data.get("device"):
                return data["device"]
        except Exception:
            pass
        return event.result if event.result else "未知"

    def _build_card_payload(self, event: NotificationEvent, device_info_display: str) -> dict:
        """构造飞书交互式卡片 payload"""
        color = self._severity_to_color(event.severity)
        if not event.severity:
            color = "red" if event.status == "失败" else "green"
        title = event.task_name

        if event.event_type == "compat_completed":
            script_count = event.extra.get("script_count")
            device_count = event.extra.get("device_count")
            if device_count and script_count:
                task_stats = f"{device_count}设备/{script_count}脚本"
            elif script_count:
                task_stats = f"{script_count}脚本"
            else:
                task_stats = event.extra.get("script_stats", "-")
            content = (
                f"**状态：**{event.status}\n"
                f"**任务统计：**{task_stats}\n"
                f"**设备列表：**{event.extra.get('device_list') or device_info_display}\n"
                f"**测试时间：**{event.completed_at}"
            )
        else:
            if event.event_type == "compat_device_completed":
                title = device_info_display
            content = (
                f"**状态：**{event.status}\n"
                f"**测试设备：**{device_info_display}\n"
                f"**测试时间：**{event.completed_at}"
            )

        if event.event_type == "compat_device_completed" and event.extra.get("warning_description"):
            content += f"\n**检查结果：**{event.extra['warning_description']}"

        report_url = self._absolute_report_url(event)
        return {
            "msg_type": "interactive",
            "card": {
                "config": {"enable_forward": True, "update_multi": False},
                "header": {
                    "template": color,
                    "title": {"tag": "plain_text", "content": f"【Agent】🤖 {title}"},
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": content}},
                    {"tag": "hr"},
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "测试报告详情"},
                                "type": "primary",
                                "multi_url": {"url": report_url},
                            }
                        ],
                    },
                ],
            },
        }
