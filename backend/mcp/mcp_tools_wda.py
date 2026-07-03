"""iOS WDA MCP 工具实现"""

import time
from typing import Any, Dict, List, Optional

import requests

try:
    from backend.mcp.mcp_tools_base import (
        MCPToolsBase,
        ScreenInfo,
        UIElement,
        UITreeResult,
    )
except ImportError:
    from web_ui.backend.mcp.mcp_tools_base import (
        MCPToolsBase,
        ScreenInfo,
        UIElement,
        UITreeResult,
    )


class WDAMCPTools(MCPToolsBase):
    """iOS 设备 MCP 工具实现（通过 WebDriverAgent）"""

    def __init__(self, device_id: Optional[str] = None):
        super().__init__(device_id)
        self._screen_size: Optional[tuple[int, int]] = None
        self._element_index = 0
        self._wda_url = self._get_wda_url()

    def _get_wda_url(self) -> str:
        """获取 WDA 服务器 URL"""
        import os

        return os.environ.get("WDA_URL", "http://localhost:8100")

    def _wda_request(self, method: str, endpoint: str, data: dict = None) -> dict:
        """发送 WDA 请求"""
        url = f"{self._wda_url}{endpoint}"
        headers = {"Content-Type": "application/json"}

        try:
            if method.upper() == "GET":
                response = requests.get(url, headers=headers, timeout=30)
            elif method.upper() == "POST":
                response = requests.post(
                    url, headers=headers, json=data or {}, timeout=30
                )
            elif method.upper() == "DELETE":
                response = requests.delete(url, headers=headers, timeout=30)
            else:
                return {"error": f"Unsupported method: {method}"}

            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"HTTP {response.status_code}: {response.text}"}
        except requests.exceptions.RequestException as e:
            return {"error": f"Request failed: {str(e)}"}

    def _get_screen_size(self) -> tuple[int, int]:
        """获取屏幕分辨率"""
        if self._screen_size:
            return self._screen_size

        result = self._wda_request("GET", "/window/size")
        if result.get("value"):
            size = result["value"]
            self._screen_size = (
                int(size.get("width", 375)),
                int(size.get("height", 667)),
            )
            return self._screen_size

        self._screen_size = (375, 667)
        return self._screen_size

    def get_current_app(self) -> Dict[str, str]:
        """获取当前应用信息"""
        result = self._wda_request("GET", "/appium/app/state")
        if result.get("value"):
            return {
                "current_app": result.get("value", {}).get("bundleId", ""),
                "current_activity": "",
            }

        # 尝试获取当前页面
        result = self._wda_request("GET", "/source")
        return {"current_app": "", "current_activity": ""}

    def get_ui_tree(self) -> UITreeResult:
        """获取 UI 元素树"""
        self._element_index = 0
        width, height = self._get_screen_size()

        current_app_info = self.get_current_app()

        result = UITreeResult()
        result.screen_info = ScreenInfo(
            width=width,
            height=height,
            current_app=current_app_info.get("current_app", ""),
            current_activity=current_app_info.get("current_activity", ""),
        )

        source_result = self._wda_request("GET", "/source")
        if not source_result or "error" in source_result:
            return result

        xml_content = source_result.get("value", "")
        result.raw_xml = xml_content

        if not xml_content or "<?xml" not in xml_content:
            return result

        # WDA 返回的是 XML 格式，需要解析
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(xml_content)
            self._parse_xml_elements(root, result.elements, depth=0)
        except Exception as e:
            self.log(f"XML 解析错误: {e}")

        for i, elem in enumerate(result.elements):
            elem.index = i

        return result

    def _parse_xml_elements(self, element, elements: List[UIElement], depth: int):
        """递归解析 XML 元素"""
        elem = self._xml_element_to_ui(element, depth)
        if elem:
            elem.xpath = self._build_xpath(element)
            elements.append(elem)

        for child in list(element):
            self._parse_xml_elements(child, elements, depth + 1)

    def _xml_element_to_ui(self, element, depth: int) -> Optional[UIElement]:
        """将 XML 元素转换为 UIElement"""
        if hasattr(element, "attrib"):
            attrib = element.attrib
        else:
            attrib = {}

        text = (
            attrib.get("name", "") or attrib.get("label", "") or attrib.get("value", "")
        )
        resource_id = attrib.get("name", "")
        content_desc = attrib.get("label", "") or attrib.get("accessibilityLabel", "")
        class_name = attrib.get("type", "") or attrib.get("class", "")
        package = ""
        bounds_str = attrib.get("rect", "") or attrib.get("bounds", "")

        bounds = self._parse_bounds(bounds_str)

        # 尝试从 rect 属性获取 bounds
        if not bounds and isinstance(attrib.get("rect"), dict):
            rect = attrib["rect"]
            bounds = (
                int(rect.get("x", 0)),
                int(rect.get("y", 0)),
                int(rect.get("width", 0)) + int(rect.get("x", 0)),
                int(rect.get("height", 0)) + int(rect.get("y", 0)),
            )

        clickable = self._parse_bool(attrib.get("clickable", "false"))
        enabled = self._parse_bool(attrib.get("enabled", "true"))
        focusable = self._parse_bool(attrib.get("focusable", "false"))
        checkable = self._parse_bool(attrib.get("checkable", "false"))
        checked = self._parse_bool(attrib.get("checked", "false") or attrib.get("value", "false"))
        scrollable = self._parse_bool(attrib.get("scrollable", "false"))

        if not any([text, resource_id, content_desc, class_name]):
            return None

        return UIElement(
            index=self._element_index,
            text=text,
            resource_id=resource_id,
            content_desc=content_desc,
            class_name=class_name,
            package=package,
            bounds=bounds,
            clickable=clickable,
            enabled=enabled,
            focusable=focusable,
            checkable=checkable,
            checked=checked,
            scrollable=scrollable,
            depth=depth,
        )

    def _build_xpath(self, element) -> str:
        """构建元素的 XPath"""
        paths = []
        current = element

        while current is not None:
            if hasattr(current, "tag"):
                tag = current.tag
            else:
                tag = "element"

            if hasattr(current, "getparent"):
                parent = current.getparent()
            else:
                parent = None

            if parent is not None:
                siblings = [
                    c for c in list(parent) if hasattr(c, "tag") and c.tag == tag
                ]
                if len(siblings) > 1:
                    index = siblings.index(current) + 1
                    paths.append(f"{tag}[{index}]")
                else:
                    paths.append(tag)
            else:
                paths.append(tag)

            current = parent

        return "/" + "/".join(reversed(paths))

    def _find_elements_by(
        self, elements: List[UIElement], by: str, value: str
    ) -> List[UIElement]:
        """根据定位方式查找元素"""
        if by == "xpath":
            ordinal_results = self._find_by_content_desc_ordinal_xpath(elements, value)
            if ordinal_results is not None:
                return ordinal_results

        results = []

        for elem in elements:
            if by == "text" and elem.text == value:
                results.append(elem)
            elif by == "textContains" and value.lower() in elem.text.lower():
                results.append(elem)
            elif by == "resource-id" and elem.resource_id == value:
                results.append(elem)
            elif by == "resourceId" and elem.resource_id == value:
                results.append(elem)
            elif by == "content-desc" and elem.content_desc == value:
                results.append(elem)
            elif by == "contentDescription" and elem.content_desc == value:
                results.append(elem)
            elif by == "class" and elem.class_name == value:
                results.append(elem)
            elif by == "className" and elem.class_name == value:
                results.append(elem)
            elif by == "xpath" and elem.xpath == value:
                results.append(elem)
            elif by == "bounds":
                bounds_value = self._parse_bounds(value)
                if elem.bounds == bounds_value:
                    results.append(elem)

        return results

    def find_element(
        self, by: str, value: str, timeout: float = 0
    ) -> Optional[UIElement]:
        """查找单个元素"""
        start_time = time.time()

        while True:
            ui_tree = self.get_ui_tree()
            elements = self._find_elements_by(ui_tree.elements, by, value)

            if elements:
                return elements[0]

            if timeout > 0 and (time.time() - start_time) >= timeout:
                return None

            if timeout > 0:
                time.sleep(0.5)
            else:
                break

        return None

    def find_elements(self, by: str, value: str) -> List[UIElement]:
        """查找所有匹配的元素"""
        ui_tree = self.get_ui_tree()
        return self._find_elements_by(ui_tree.elements, by, value)

    def click_element(self, by: str, value: str) -> Dict[str, Any]:
        """直接点击元素 bounds，不在方法内部查找页面元素。"""
        if by != "bounds":
            return {
                "success": False,
                "message": "click_element 只负责直接点击 bounds，请先通过 get_ui_tree/find_element 获取元素 bounds",
            }

        try:
            bounds = self._parse_bounds(value)
        except (TypeError, ValueError):
            return {"success": False, "message": f"无效 bounds: {value}"}

        if bounds["right"] <= bounds["left"] or bounds["bottom"] <= bounds["top"]:
            return {"success": False, "message": f"无效 bounds: {value}"}

        x = (bounds["left"] + bounds["right"]) // 2
        y = (bounds["top"] + bounds["bottom"]) // 2
        result = self._wda_request("POST", "/wda/tap/withCoordinates", {"x": x, "y": y})

        if not result.get("error"):
            return {
                "success": True,
                "message": f"已点击坐标 ({x}, {y})",
                "bounds": bounds,
            }

        return {
            "success": False,
            "message": f"点击失败: {result.get('error', 'unknown')}",
        }

    def input_text(self, text: str, clear_first: bool = True) -> Dict[str, Any]:
        """输入文本"""
        result = self._wda_request("POST", "/wda/keys", {"value": list(text)})

        if not result.get("error"):
            return {"success": True, "message": f"已输入文本: {text}"}

        return {
            "success": False,
            "message": f"输入失败: {result.get('error', 'unknown')}",
        }

    def swipe(self, direction: str, distance: str = "50%") -> Dict[str, Any]:
        """滑动操作"""
        width, height = self._get_screen_size()

        try:
            dist_percent = int(distance.rstrip("%")) / 100
        except ValueError:
            dist_percent = 0.5

        dist_px = int(min(width, height) * dist_percent)

        cx, cy = width // 2, height // 2

        if direction == "up":
            start_x, start_y = cx, cy + dist_px // 2
            end_x, end_y = cx, cy - dist_px // 2
        elif direction == "down":
            start_x, start_y = cx, cy - dist_px // 2
            end_x, end_y = cx, cy + dist_px // 2
        elif direction == "left":
            start_x, start_y = cx + dist_px // 2, cy
            end_x, end_y = cx - dist_px // 2, cy
        elif direction == "right":
            start_x, start_y = cx - dist_px // 2, cy
            end_x, end_y = cx + dist_px // 2, cy
        else:
            return {"success": False, "message": f"未知方向: {direction}"}

        result = self._wda_request(
            "POST",
            "/wda/dragfromtoforduration",
            {
                "fromX": start_x,
                "fromY": start_y,
                "toX": end_x,
                "toY": end_y,
                "duration": 0.5,
            },
        )

        if not result.get("error"):
            return {"success": True, "message": f"已滑动 {direction}"}

        return {
            "success": False,
            "message": f"滑动失败: {result.get('error', 'unknown')}",
        }

    def long_press(self, by: str, value: str, duration: int = 1000) -> Dict[str, Any]:
        """长按操作"""
        element = self.find_element(by, value, timeout=2)

        if not element:
            return {"success": False, "message": f"未找到元素: {by}={value}"}

        x, y = element.get_center()
        result = self._wda_request(
            "POST", "/wda/touchAndHold", {"x": x, "y": y, "duration": duration / 1000.0}
        )

        if not result.get("error"):
            return {
                "success": True,
                "message": f"已长按坐标 ({x}, {y})",
                "element": element.to_dict(),
            }

        return {
            "success": False,
            "message": f"长按失败: {result.get('error', 'unknown')}",
        }

    def press_element(self, by: str, value: str) -> Dict[str, Any]:
        """按压元素（用于确认等场景，与点击相同）"""
        return self.click_element(by, value)

    def back(self) -> Dict[str, Any]:
        """返回操作"""
        result = self._wda_request("POST", "/wda/back")

        if not result.get("error"):
            return {"success": True, "message": "已执行返回"}

        return {
            "success": False,
            "message": f"返回失败: {result.get('error', 'unknown')}",
        }

    def home(self) -> Dict[str, Any]:
        """回到桌面"""
        result = self._wda_request("POST", "/wda/home")

        if not result.get("error"):
            return {"success": True, "message": "已回到桌面"}

        return {
            "success": False,
            "message": f"回到桌面失败: {result.get('error', 'unknown')}",
        }

    def launch_app(
        self, app_name: str, package_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """启动应用"""
        bundle_id = package_name or app_name
        result = self._wda_request("POST", "/wda/launchApp", {"bundleId": bundle_id})

        if not result.get("error"):
            return {"success": True, "message": f"已启动应用: {app_name}"}

        return {
            "success": False,
            "message": f"启动失败: {result.get('error', 'unknown')}",
        }

    def wait(self, duration: float = 2.0) -> Dict[str, Any]:
        """等待"""
        time.sleep(duration)
        return {"success": True, "message": f"已等待 {duration} 秒"}

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """获取工具定义（OpenAI Function Calling 格式）"""
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_ui_tree",
                    "description": "获取当前屏幕的 UI 元素树，返回所有可交互元素的列表",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_element",
                    "description": "根据指定条件查找 UI 元素",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "by": {
                                "type": "string",
                                "enum": [
                                    "resource-id",
                                    "text",
                                    "textContains",
                                    "content-desc",
                                    "xpath",
                                    "class",
                                ],
                                "description": "元素定位方式",
                            },
                            "value": {"type": "string", "description": "定位值"},
                            "timeout": {
                                "type": "number",
                                "description": "等待超时（秒）",
                                "default": 0,
                            },
                        },
                        "required": ["by", "value"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "click_element",
                    "description": "点击指定的 UI 元素",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "by": {
                                "type": "string",
                                "enum": [
                                    "resource-id",
                                    "text",
                                    "textContains",
                                    "content-desc",
                                    "xpath",
                                    "bounds",
                                ],
                                "description": "元素定位方式",
                            },
                            "value": {"type": "string", "description": "定位值"},
                        },
                        "required": ["by", "value"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "input_text",
                    "description": "在当前聚焦的输入框中输入文本",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {
                                "type": "string",
                                "description": "要输入的文本内容",
                            },
                            "clear_first": {
                                "type": "boolean",
                                "description": "是否先清空输入框",
                                "default": True,
                            },
                        },
                        "required": ["text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "swipe",
                    "description": "执行滑动操作",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "direction": {
                                "type": "string",
                                "enum": ["up", "down", "left", "right"],
                                "description": "滑动方向",
                            },
                            "distance": {
                                "type": "string",
                                "description": "滑动距离比例，如 '50%'",
                                "default": "50%",
                            },
                        },
                        "required": ["direction"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "long_press",
                    "description": "长按指定元素",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "by": {"type": "string", "description": "元素定位方式"},
                            "value": {"type": "string", "description": "定位值"},
                            "duration": {
                                "type": "integer",
                                "description": "长按时长（毫秒）",
                                "default": 1000,
                            },
                        },
                        "required": ["by", "value"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "press_element",
                    "description": "按压元素（与点击相同）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "by": {"type": "string", "description": "元素定位方式"},
                            "value": {"type": "string", "description": "定位值"},
                        },
                        "required": ["by", "value"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "back",
                    "description": "执行返回操作",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "home",
                    "description": "返回桌面",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "launch_app",
                    "description": "启动指定应用",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "app_name": {"type": "string", "description": "应用名称"},
                            "package_name": {
                                "type": "string",
                                "description": "包名（可选）",
                            },
                        },
                        "required": ["app_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "wait",
                    "description": "等待指定时间",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "duration": {
                                "type": "number",
                                "description": "等待时长（秒）",
                                "default": 2.0,
                            }
                        },
                        "required": [],
                    },
                },
            },
        ]
