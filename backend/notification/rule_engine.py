"""通知规则引擎 — 从 DB 加载规则并匹配事件"""
import json
import logging

from backend.db.database import get_enabled_rules
from backend.notification.base import NotificationEvent

logger = logging.getLogger(__name__)


class NotificationRuleEngine:
    """匹配 NotificationEvent 到目标通知通道列表"""

    def match(self, event: NotificationEvent) -> list[str]:
        """根据事件匹配规则，返回目标通道列表（并集）"""
        rules = get_enabled_rules()
        matched_channels = set()
        for rule in rules:
            if rule["event_type"] != event.event_type:
                continue
            conditions = json.loads(rule["conditions"]) if rule["conditions"] else {}
            if not self._conditions_match(conditions, event):
                continue
            channels = json.loads(rule["channels"]) if rule["channels"] else []
            matched_channels.update(channels)
        return list(matched_channels)

    def _conditions_match(self, conditions: dict, event: NotificationEvent) -> bool:
        """检查所有条件 key-value 是否匹配 event 对应字段"""
        event_dict = event.to_dict()
        for key, value in conditions.items():
            if event_dict.get(key) != value:
                return False
        return True
