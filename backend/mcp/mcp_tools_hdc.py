"""HarmonyOS HDC MCP 工具实现"""

import subprocess
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

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


class HDCMCPTools(MCPToolsBase):
    """HarmonyOS 设备 MCP 工具实现"""

    def __init__(self, device_id: Optional[str] = None):
        super().__init__(device_id)
        self._screen_size: Optional[tuple[int, int]] = None
        self._element_index = 0

    def _run_hdc_command(self, command: str, timeout: int = 30) -> str:
        """执行 HDC 命令"""
        cmd = ["hdc"]
        if self.device_id:
            cmd += ["-s", self.device_id]
        cmd += command.split()

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            return result.stdout.strip() if result.stdout else result.stderr.strip()
        except subprocess.TimeoutExpired:
            return f"Command timed out: {command}"
        except Exception as e:
            return f"Error: {str(e)}"

    def _run_hdc_shell(self, shell_command: str, timeout: int = 30) -> str:
        """执行 HDC Shell 命令"""
        return self._run_hdc_command(f"shell {shell_command}", timeout)

    def _get_screen_size(self) -> tuple[int, int]:
        """获取屏幕分辨率"""
        if self._screen_size:
            return self._screen_size

        output = self._run_hdc_shell("wm size")
        if output and "x" in output:
            parts = output.split("x")
            if len(parts) == 2:
                width = int(parts[0].split(":")[-1].strip())
                height = int(parts[1].strip())
                self._screen_size = (width, height)
                return self._screen_size

        output = self._run_hdc_shell("dumpsys display | grep mBaseDisplayInfo")
        if output:
            parts = output.split()
            for i, part in enumerate(parts):
                if "width=" in part:
                    width = int(part.split("=")[1])
                    height = int(parts[i + 1].split("=")[1])
                    self._screen_size = (width, height)
                    return self._screen_size

        self._screen_size = (1080, 1920)
        return self._screen_size

    def get_current_app(self) -> Dict[str, str]:
        """获取当前应用信息"""
        output = self._run_hdc_shell("dumpsys window | grep mCurrentFocus")
        current_app = ""
        current_activity = ""

        if output:
            parts = output.split()
            if len(parts) >= 2:
                full_name = parts[-1].strip().rstrip("}")
                if "/" in full_name:
                    app_parts = full_name.rsplit("/", 1)
                    current_app = app_parts[0]
                    current_activity = app_parts[1].rstrip("}")
                else:
                    current_app = full_name

        return {
            "current_app": current_app,
            "current_activity": current_activity,
        }

    def get_ui_tree(self) -> UITreeResult:
        """获取 UI 元素树（通过 uiautomator dump）"""
        self._element_index = 0
        width, height = self._get_screen_size()

        current_app_info = self.get_current_app()

        xml_content = self._run_hdc_shell(
            "uiautomator dump /sdcard/ui_dump.xml && cat /sdcard/ui_dump.xml"
        )

        self._run_hdc_shell("rm /sdcard/ui_dump.xml")

        result = UITreeResult()
        result.screen_info = ScreenInfo(
            width=width,
            height=height,
            current_app=current_app_info.get("current_app", ""),
            current_activity=current_app_info.get("current_activity", ""),
        )
        result.raw_xml = xml_content

        if not xml_content or "<?xml" not in xml_content:
            return result

        try:
            root = ET.fromstring(xml_content)
            self._parse_xml_elements(root, result.elements, depth=0)
        except ET.ParseError as e:
            self.log(f"XML 解析错误: {e}")

        for i, elem in enumerate(result.elements):
            elem.index = i

        return result

    def _parse_xml_elements(
        self,
        element: ET.Element,
        elements: List[UIElement],
        depth: int,
        xpath: str = "",
    ):
        """递归解析 XML 元素"""
        elem = self._xml_element_to_ui(element, depth)
        if elem:
            elem.xpath = xpath
            elements.append(elem)

        child_counts: Dict[str, int] = {}
        for child in element:
            child_counts[child.tag] = child_counts.get(child.tag, 0) + 1
            child_xpath = (
                f"{xpath}/{child.tag}[{child_counts[child.tag]}]"
                if xpath
                else f"/{child.tag}[{child_counts[child.tag]}]"
            )
            self._parse_xml_elements(child, elements, depth + 1, child_xpath)

    def _xml_element_to_ui(
        self, element: ET.Element, depth: int
    ) -> Optional[UIElement]:
        """将 XML 元素转换为 UIElement"""
        attrib = element.attrib

        text = attrib.get("text", "")
        resource_id = attrib.get("resource-id", "")
        content_desc = attrib.get("content-desc", "")
        class_name = attrib.get("class", "")
        package = attrib.get("package", "")
        bounds_str = attrib.get("bounds", "")

        if not any([text, resource_id, content_desc, class_name]):
            return None

        try:
            bounds = self._parse_bounds(bounds_str)
        except (TypeError, ValueError):
            return None

        clickable = self._parse_bool(attrib.get("clickable", "false"))
        enabled = self._parse_bool(attrib.get("enabled", "true"))
        focusable = self._parse_bool(attrib.get("focusable", "false"))
        checkable = self._parse_bool(attrib.get("checkable", "false"))
        checked = self._parse_bool(attrib.get("checked", "false"))
        scrollable = self._parse_bool(attrib.get("scrollable", "false"))

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

    def _build_xpath(self, element: ET.Element) -> str:
        """构建元素的 XPath（ElementTree 无父指针时返回当前节点路径）"""
        return f"/{element.tag}[1]"

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
        output = self._run_hdc_shell(f"input tap {x} {y}")

        if not output:
            return {
                "success": True,
                "message": f"已点击坐标 ({x}, {y})",
                "bounds": bounds,
            }

        return {"success": False, "message": f"点击失败: {output}"}

    def input_text(self, text: str, clear_first: bool = True) -> Dict[str, Any]:
        """输入文本"""
        if clear_first:
            self._run_hdc_shell("input keyevent KEYCODE_CTRL_A")
            time.sleep(0.1)

        text = text.replace(" ", "%s")
        output = self._run_hdc_shell(f"input text {text}")

        if not output:
            return {"success": True, "message": f"已输入文本: {text}"}

        return {"success": False, "message": f"输入失败: {output}"}

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

        output = self._run_hdc_shell(f"input swipe {start_x} {start_y} {end_x} {end_y}")

        if not output:
            return {"success": True, "message": f"已滑动 {direction}"}

        return {"success": False, "message": f"滑动失败: {output}"}

    def long_press(self, by: str, value: str, duration: int = 1000) -> Dict[str, Any]:
        """长按操作"""
        element = self.find_element(by, value, timeout=2)

        if not element:
            return {"success": False, "message": f"未找到元素: {by}={value}"}

        x, y = element.get_center()
        output = self._run_hdc_shell(f"input touchscreen longpress {x} {y} {duration}")

        if not output:
            return {
                "success": True,
                "message": f"已长按坐标 ({x}, {y})",
                "element": element.to_dict(),
            }

        return {"success": False, "message": f"长按失败: {output}"}

    def press_element(self, by: str, value: str) -> Dict[str, Any]:
        """按压元素（用于确认等场景，与点击相同）"""
        return self.click_element(by, value)

    def back(self) -> Dict[str, Any]:
        """返回操作"""
        output = self._run_hdc_shell("input keyevent KEYCODE_BACK")

        if not output:
            return {"success": True, "message": "已执行返回"}

        return {"success": False, "message": f"返回失败: {output}"}

    def home(self) -> Dict[str, Any]:
        """回到桌面"""
        output = self._run_hdc_shell("input keyevent KEYCODE_HOME")

        if not output:
            return {"success": True, "message": "已回到桌面"}

        return {"success": False, "message": f"回到桌面失败: {output}"}

    def launch_app(
        self, app_name: str, package_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """启动应用"""
        if package_name:
            launch_command = f"am start -n {package_name}/.MainAbility"
        else:
            launch_command = f"aa start -a {app_name}"

        output = self._run_hdc_shell(launch_command)

        if "Starting" in output or "aa start" in output or not output:
            return {"success": True, "message": f"已启动应用: {app_name}"}

        return {"success": False, "message": f"启动失败: {output}"}

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
