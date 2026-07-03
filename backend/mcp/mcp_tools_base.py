"""MCP 工具基类 - 定义标准化 MCP 工具接口"""

import re
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class UIElement:
    """UI 元素数据结构"""
    index: int = 0
    text: str = ""
    resource_id: str = ""
    content_desc: str = ""
    class_name: str = ""
    package: str = ""
    bounds: Dict[str, int] = field(default_factory=lambda: {"left": 0, "top": 0, "right": 0, "bottom": 0})
    clickable: bool = False
    enabled: bool = True
    focusable: bool = False
    checkable: bool = False
    checked: bool = False
    scrollable: bool = False
    depth: int = 0
    xpath: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "text": self.text,
            "resource_id": self.resource_id,
            "content_desc": self.content_desc,
            "class": self.class_name,
            "package": self.package,
            "bounds": self.bounds,
            "clickable": self.clickable,
            "enabled": self.enabled,
            "focusable": self.focusable,
            "checkable": self.checkable,
            "checked": self.checked,
            "scrollable": self.scrollable,
            "depth": self.depth,
            "xpath": self.xpath,
        }

    def get_center(self) -> tuple[int, int]:
        """获取元素中心坐标"""
        left = self.bounds.get("left", 0)
        top = self.bounds.get("top", 0)
        right = self.bounds.get("right", 0)
        bottom = self.bounds.get("bottom", 0)
        return (left + right) // 2, (top + bottom) // 2


@dataclass
class ScreenInfo:
    """屏幕信息"""
    width: int = 1080
    height: int = 1920
    current_app: str = ""
    current_activity: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "current_app": self.current_app,
            "current_activity": self.current_activity,
        }


@dataclass
class UITreeResult:
    """UI 树查询结果"""
    elements: List[UIElement] = field(default_factory=list)
    screen_info: ScreenInfo = field(default_factory=ScreenInfo)
    raw_xml: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "elements": [e.to_dict() for e in self.elements],
            "screen_info": self.screen_info.to_dict(),
            "raw_xml": self.raw_xml,
        }

    def format_text(self) -> str:
        """格式化输出为易读的文本形式"""
        lines = []
        lines.append(f"=== 屏幕信息 ===")
        lines.append(f"分辨率: {self.screen_info.width}x{self.screen_info.height}")
        lines.append(f"当前应用: {self.screen_info.current_app}")
        lines.append(f"当前界面: {self.screen_info.current_activity}")
        lines.append(f"\n=== UI 元素 ({len(self.elements)} 个) ===")

        for elem in self.elements:
            lines.append(f"[{elem.index}] {elem.class_name}")
            if elem.text:
                lines.append(f"    text: {elem.text}")
            if elem.resource_id:
                lines.append(f"    resource-id: {elem.resource_id}")
            if elem.content_desc:
                lines.append(f"    content-desc: {elem.content_desc}")
            bounds_str = f"({elem.bounds['left']},{elem.bounds['top']})-({elem.bounds['right']},{elem.bounds['bottom']})"
            lines.append(f"    bounds: {bounds_str}")
            lines.append(f"    clickable: {elem.clickable}, enabled: {elem.enabled}")
            if elem.xpath:
                lines.append(f"    xpath: {elem.xpath}")
            lines.append("")

        return "\n".join(lines)


class MCPToolsBase(ABC):
    """MCP 工具基类 - 定义标准化接口"""

    def __init__(self, device_id: Optional[str] = None):
        self.device_id = device_id
        self._log_callback: Optional[Callable[[str], None]] = None

    def set_log_callback(self, callback: Callable[[str], None]):
        """设置日志回调"""
        self._log_callback = callback

    def log(self, message: str):
        """输出日志"""
        if self._log_callback:
            self._log_callback(message)

    @abstractmethod
    def get_ui_tree(self) -> UITreeResult:
        """获取 UI 元素树"""
        pass

    @abstractmethod
    def find_element(
        self,
        by: str,
        value: str,
        timeout: float = 0
    ) -> Optional[UIElement]:
        """
        查找单个元素

        Args:
            by: 定位方式 (text/textContains/resource-id/content-desc/xpath/class/bounds)
            value: 定位值
            timeout: 等待超时（秒），0 表示不等待

        Returns:
            找到的元素，未找到返回 None
        """
        pass

    def find_element_with_fallback(
        self,
        strategies: List[Dict[str, Any]],
        timeout: float = 5
    ) -> Dict[str, Any]:
        """
        使用多种策略查找元素（备选定位策略）

        Args:
            strategies: 定位策略列表，按优先级排序
                例如: [
                    {"by": "text", "value": "登录"},
                    {"by": "textContains", "value": "登"},
                    {"by": "resource-id", "value": "com.app:id/btn_login"},
                    {"by": "content-desc", "value": "登录按钮"},
                ]
            timeout: 总超时时间（秒）

        Returns:
            {
                "success": bool,
                "element": UIElement 或 None,
                "strategy_used": str,  # 实际使用的策略
                "all_strategies_tried": List[Dict]  # 所有尝试过的策略
            }
        """
        start_time = time.time()
        tried_strategies = []

        for strategy in strategies:
            if time.time() - start_time >= timeout:
                break

            by = strategy.get("by", "")
            value = strategy.get("value", "")

            if not by or not value:
                continue

            tried_strategies.append({"by": by, "value": value, "status": "tried"})

            element = self.find_element(by, value, timeout=1)

            if element:
                return {
                    "success": True,
                    "element": element,
                    "strategy_used": {"by": by, "value": value},
                    "all_strategies_tried": tried_strategies
                }

        return {
            "success": False,
            "element": None,
            "strategy_used": None,
            "all_strategies_tried": tried_strategies
        }

    @abstractmethod
    def find_elements(
        self,
        by: str,
        value: str
    ) -> List[UIElement]:
        """
        查找所有匹配的元素

        Args:
            by: 定位方式
            value: 定位值

        Returns:
            匹配的元素列表
        """
        pass

    @abstractmethod
    def click_element(
        self,
        by: str,
        value: str
    ) -> Dict[str, Any]:
        """
        点击元素

        Args:
            by: 定位方式
            value: 定位值

        Returns:
            {"success": bool, "message": str}
        """
        pass

    @abstractmethod
    def input_text(
        self,
        text: str,
        clear_first: bool = True
    ) -> Dict[str, Any]:
        """
        输入文本

        Args:
            text: 要输入的文本
            clear_first: 是否先清空

        Returns:
            {"success": bool, "message": str}
        """
        pass

    @abstractmethod
    def swipe(
        self,
        direction: str,
        distance: str = "50%"
    ) -> Dict[str, Any]:
        """
        滑动操作

        Args:
            direction: 方向 (up/down/left/right)
            distance: 距离百分比

        Returns:
            {"success": bool, "message": str}
        """
        pass

    @abstractmethod
    def long_press(
        self,
        by: str,
        value: str,
        duration: int = 1000
    ) -> Dict[str, Any]:
        """
        长按操作

        Args:
            by: 定位方式
            value: 定位值
            duration: 长按时长（毫秒）

        Returns:
            {"success": bool, "message": str}
        """
        pass

    @abstractmethod
    def back(self) -> Dict[str, Any]:
        """返回操作"""
        pass

    @abstractmethod
    def home(self) -> Dict[str, Any]:
        """回到桌面"""
        pass

    @abstractmethod
    def launch_app(
        self,
        app_name: str,
        package_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        启动应用

        Args:
            app_name: 应用名称
            package_name: 应用包名（可选）

        Returns:
            {"success": bool, "message": str}
        """
        pass

    @abstractmethod
    def get_current_app(self) -> Dict[str, str]:
        """获取当前应用信息"""
        pass

    @abstractmethod
    def wait(self, duration: float = 2.0) -> Dict[str, Any]:
        """等待"""
        pass

    @abstractmethod
    def press_element(
        self,
        by: str,
        value: str
    ) -> Dict[str, Any]:
        """
        按压元素（用于确认等场景）

        Args:
            by: 定位方式
            value: 定位值

        Returns:
            {"success": bool, "message": str}
        """
        pass

    @abstractmethod
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """获取工具定义（OpenAI Function Calling 格式）"""
        pass

    def _parse_bounds(self, bounds_str: Any) -> Dict[str, int]:
        """解析 bounds 输入为 left/top/right/bottom 字典。"""
        if isinstance(bounds_str, dict):
            keys = ("left", "top", "right", "bottom")
            if all(key in bounds_str for key in keys):
                return {key: int(bounds_str[key]) for key in keys}
            raise ValueError(f"无效 bounds: {bounds_str}")

        if isinstance(bounds_str, (list, tuple)):
            parts = list(bounds_str)
        else:
            text = str(bounds_str).strip()
            text = text.strip("()[]")
            text = text.replace("][", ",")
            text = text.replace(")-(", ",")
            parts = [part.strip() for part in text.split(",") if part.strip()]

        if len(parts) != 4:
            raise ValueError(f"无效 bounds: {bounds_str}")

        left, top, right, bottom = (int(part) for part in parts)
        return {"left": left, "top": top, "right": right, "bottom": bottom}

    def _find_by_content_desc_ordinal_xpath(
        self, elements: List[UIElement], value: str
    ) -> Optional[List[UIElement]]:
        """解析形如 (//*[@content-desc="name"])[2] 的窄范围 XPath。"""
        match = re.fullmatch(
            r"""\(\s*//\*\[@content-desc=(["'])(.*?)\1\]\s*\)\[(\d+)\]""",
            value.strip(),
        )
        if not match:
            return None

        content_desc = match.group(2)
        ordinal = int(match.group(3))
        if ordinal < 1:
            return []

        matches = [elem for elem in elements if elem.content_desc == content_desc]
        if ordinal > len(matches):
            return []

        return [matches[ordinal - 1]]

    def _generate_xpath(self, element: ET.Element, path: str = "") -> str:
        """为元素生成 XPath"""
        tag = element.tag
        index = 1
        for sibling in element.parent.iter(tag) if element.parent is not None else [element]:
            if sibling == element:
                break
            index += 1

        current_path = f"{path}/{tag}[{index}]"
        if element.parent is not None:
            return self._generate_xpath(element.parent, current_path)
        return current_path

    def _parse_bool(self, value: str) -> bool:
        """解析布尔值"""
        return value.lower() in ("true", "1", "yes", "true")
