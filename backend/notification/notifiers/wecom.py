"""企微通知通道（预留，未实现）"""
import logging

from backend.notification.base import BaseNotifier, NotificationEvent

logger = logging.getLogger(__name__)


class WeComNotifier(BaseNotifier):
    name = "wecom"

    def get_default_template(self) -> str:
        return "任务: {{task_name}}\n状态: {{status}}\n设备: {{device_info}}\n时间: {{completed_at}}"

    async def send(self, event: NotificationEvent) -> bool:
        logger.info(f"企微通知通道暂未实现，跳过发送: {event.task_name}")
        return True

    async def test_connection(self) -> bool:
        logger.info("企微通道暂未实现，测试跳过")
        return False
