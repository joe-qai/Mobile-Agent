"""通知系统核心抽象 — NotificationEvent 数据模型 + BaseNotifier 接口"""
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field


@dataclass
class NotificationEvent:
    event_type: str    # task_failed / task_completed / compat_completed
    task_name: str
    status: str        # 成功 / 失败 / 阻塞 / 严重 / 警告 / 建议
    severity: str      # compat 最高级别: blocker / major / minor / suggestion
    completed_at: str
    result: str
    device_id: str
    task_type: str     # script / compatibility / normal
    role: str          # parent / child
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class BaseNotifier(ABC):
    name: str = ""

    @abstractmethod
    async def send(self, event: NotificationEvent) -> bool:
        ...

    @abstractmethod
    async def test_connection(self) -> bool:
        ...

    @abstractmethod
    def get_default_template(self) -> str:
        ...
