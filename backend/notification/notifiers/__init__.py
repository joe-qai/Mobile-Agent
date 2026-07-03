"""通知通道注册表"""
from backend.notification.notifiers.dingtalk import DingTalkNotifier
from backend.notification.notifiers.email import EmailNotifier
from backend.notification.notifiers.feishu import FeishuNotifier
from backend.notification.notifiers.wecom import WeComNotifier

NOTIFIERS = {
    "feishu": FeishuNotifier,
    "dingtalk": DingTalkNotifier,
    "wecom": WeComNotifier,
    "email": EmailNotifier,
}


def get_notifier(name: str):
    cls = NOTIFIERS.get(name)
    return cls() if cls else None
