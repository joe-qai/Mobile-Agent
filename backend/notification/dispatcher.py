"""通知分发器 — 统一入口: event → rule match → parallel send"""
import asyncio
import logging

from backend.db.database import (
    create_notification_rule,
    get_notification_rules,
    log_notification,
)
from backend.notification.base import NotificationEvent
from backend.notification.notifiers import get_notifier
from backend.notification.rule_engine import NotificationRuleEngine

logger = logging.getLogger(__name__)


class NotificationDispatcher:
    """统一通知入口"""

    def __init__(self):
        self.rule_engine = NotificationRuleEngine()

    async def notify(self, event: NotificationEvent) -> dict[str, bool]:
        """分发通知到匹配的通道，返回 {channel: success}"""
        channels = self.rule_engine.match(event)
        if not channels:
            return {}

        tasks = []
        for channel_name in channels:
            notifier = get_notifier(channel_name)
            if notifier is None:
                logger.warning(f"通知通道 {channel_name} 未注册，跳过")
                continue
            tasks.append((channel_name, self._send_and_log(channel_name, notifier, event)))

        if not tasks:
            return {}

        results = await asyncio.gather(
            *[t[1] for t in tasks], return_exceptions=True
        )

        out = {}
        for idx, (channel_name, _) in enumerate(tasks):
            result = results[idx]
            if isinstance(result, Exception):
                logger.error(f"通知通道 {channel_name} 发送异常: {result}")
                out[channel_name] = False
            else:
                out[channel_name] = result
        return out

    async def _send_and_log(self, channel_name: str, notifier, event: NotificationEvent) -> bool:
        """发送通知并记录日志"""
        try:
            success = await notifier.send(event)
        except Exception as e:
            logger.error(f"通道 {channel_name} send 抛出异常: {e}")
            success = False
        status = "sent" if success else "failed"
        log_notification(
            rule_id=0,
            channel=channel_name,
            event_type=event.event_type,
            task_name=event.task_name,
            status=status,
            error_msg="" if success else "send failed",
        )
        return success

    def init_default_rules(self):
        """初始化默认通知规则（如 DB 中不存在）"""
        existing = {r["name"] for r in get_notification_rules()}
        defaults = [
            ("子任务脚本失败通知", "task_failed", '{"task_type":"script","role":"child"}', '["feishu"]', 10),
            ("普通测试脚本失败通知", "task_failed", '{"task_type":"normal"}', '["feishu"]', 20),
            ("普通测试完成通知", "task_completed", '{}', '["feishu"]', 25),
            ("兼容性设备完成通知", "compat_device_completed", '{"task_type":"compatibility","role":"child"}', '["feishu"]', 28),
            ("兼容性测试完成通知", "compat_completed", '{}', '["feishu"]', 30),
        ]
        for name, event_type, conditions, channels, priority in defaults:
            if name not in existing:
                create_notification_rule(name, event_type, conditions, channels, True, priority)
                logger.info(f"初始化默认通知规则: {name}")


notification_dispatcher = NotificationDispatcher()
