"""Android ADB MCP 工具实现"""

import base64
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Dict, List, Optional

from PIL import Image

from backend.config.apps import get_package_name

try:
    from backend.mcp.mcp_tools_base import (
        MCPToolsBase,
        ScreenInfo,
        UIElement,
        UITreeResult,
    )
except ImportError:
    from backend.mcp.mcp_tools_base import (
        MCPToolsBase,
        ScreenInfo,
        UIElement,
        UITreeResult,
    )


@dataclass
class Screenshot:
    base64_data: str
    width: int
    height: int
    is_sensitive: bool = False
    is_fallback: bool = False


def get_screenshot(device_id: str | None = None, timeout: int = 10) -> Screenshot:
    """通过 ADB screencap 截取屏幕"""
    adb_prefix = ["adb"]
    if device_id:
        adb_prefix += ["-s", device_id]

    try:
        result = subprocess.run(
            adb_prefix + ["exec-out", "screencap", "-p"],
            capture_output=True,
            timeout=timeout,
        )
        raw_png = result.stdout or b""
        if not raw_png:
            return Screenshot(base64_data="", width=0, height=0, is_fallback=True)

        with Image.open(BytesIO(raw_png)) as img:
            width, height = img.size
            buf = BytesIO()
            img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return Screenshot(base64_data=b64, width=width, height=height)
    except Exception:
        return Screenshot(base64_data="", width=0, height=0, is_fallback=True)


class DeviceInfo:
    """设备信息类"""

    def __init__(
        self,
        id: str,
        name: str = "",
        brand: str = "",
        model: str = "",
        version: str = "",
        status: str = "",
        connection_type: str = "usb",
        ip: str = None,
        port: str = None,
        usb_parent_id: str = None,
        wifi_enabled: bool = False,
        wifi_ip: str = None,
        platform: str = "android",
    ):
        self.id = id
        self.name = name
        self.brand = brand
        self.model = model
        self.version = version
        self.status = status
        self.connection_type = connection_type
        self.ip = ip
        self.port = port
        self.usb_parent_id = usb_parent_id
        self.wifi_enabled = wifi_enabled
        self.wifi_ip = wifi_ip
        self.platform = platform

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "id": self.id,
            "name": self.name,
            "brand": self.brand,
            "model": self.model,
            "version": self.version,
            "status": self.status,
            "connection_type": self.connection_type,
            "ip": self.ip,
            "port": self.port,
            "usb_parent_id": self.usb_parent_id,
            "wifi_enabled": self.wifi_enabled,
            "wifi_ip": self.wifi_ip,
            "platform": self.platform,
        }


class ADBMCTools(MCPToolsBase):
    """Android 设备 MCP 工具实现"""

    def __init__(self, device_id: Optional[str] = None):
        super().__init__(device_id)
        self._screen_size: Optional[tuple[int, int]] = None
        self._element_index = 0
        self._u2_device = None
        self._u2_connect_time = 0
        self._u2_connect_timeout = 60
        self._cached_ui_tree = None
        self._cached_ui_tree_time = 0
        self._ui_tree_cache_timeout = 2.0

    def _get_u2_device(self, force_reconnect: bool = False) -> Any:
        """获取或复用 uiautomator2 设备连接"""
        try:
            import uiautomator2 as u2
        except ImportError:
            self.log("uiautomator2 库未安装")
            return None

        now = time.time()

        if not force_reconnect and self._u2_device:
            if now - self._u2_connect_time < self._u2_connect_timeout:
                try:
                    self._u2_device.info
                    return self._u2_device
                except Exception:
                    self.log("复用 uiautomator2 连接失败，重新连接")

        self.log(f"通过 uiautomator2 连接设备 (device_id={self.device_id})")

        try:
            if self.device_id:
                device = u2.connect(self.device_id)
            else:
                device = u2.connect()

            device.info
            self._u2_device = device
            self._u2_connect_time = now
            self.log("uiautomator2 设备连接成功")
            return device
        except Exception as e:
            self.log(f"uiautomator2 连接失败: {str(e)}")
            self._u2_device = None
            return None

    def _run_adb_command(self, command: str, timeout: int = 30) -> str:
        """执行 ADB 命令"""
        cmd = ["adb"]
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

    def _get_adb_prefix(self) -> list:
        """获取 ADB 命令前缀列表（包含设备ID）"""
        prefix = ["adb"]
        if self.device_id:
            prefix += ["-s", self.device_id]
        return prefix

    def _run_adb_shell(self, shell_command: str, timeout: int = 30) -> str:
        """执行 ADB Shell 命令"""
        return self._run_adb_command(f"shell {shell_command}", timeout)

    def _get_device_prop(self, device_id: str, prop: str) -> str:
        result = subprocess.run(
            ["adb", "-s", device_id, "shell", "getprop", prop],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        return result.stdout.strip() if result.stdout else ""

    def _get_screen_size(self) -> tuple[int, int]:
        """获取屏幕分辨率"""
        if self._screen_size:
            return self._screen_size

        output = self._run_adb_shell("wm size")
        if output and "x" in output:
            parts = output.split("x")
            if len(parts) == 2:
                width = int(parts[0].split(":")[-1].strip())
                height = int(parts[1].strip())
                self._screen_size = (width, height)
                return self._screen_size

        output = self._run_adb_shell("dumpsys display | grep mBaseDisplayInfo")
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

    def get_current_app(self) -> Dict[str, Any]:
        """获取当前应用信息"""
        try:
            output = self._run_adb_shell("dumpsys window | grep mCurrentFocus")
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
                "success": True,
                "current_app": current_app,
                "current_activity": current_activity,
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"获取当前应用失败: {str(e)}",
                "current_app": "",
                "current_activity": "",
            }

    def get_screen_info(self) -> Dict[str, Any]:
        """Get screen size and orientation."""
        try:
            device = self._get_u2_device()
            if device:
                width, height = device.window_size()
                return {
                    "success": True,
                    "width": int(width),
                    "height": int(height),
                    "orientation": getattr(device, "orientation", ""),
                }

            width, height = self._get_screen_size()
            return {
                "success": True,
                "width": int(width),
                "height": int(height),
                "orientation": "",
            }
        except Exception as e:
            return {"success": False, "message": f"get_screen_info failed: {str(e)}"}

    def app_current(self) -> Dict[str, Any]:
        """Get current foreground app using uiautomator2."""
        try:
            device = self._get_u2_device()
            if not device:
                return self.get_current_app()

            current = device.app_current() or {}
            package_name = current.get("package", "")
            activity = current.get("activity", "")
            return {
                "success": True,
                "current_app": package_name,
                "current_activity": activity,
                "package": package_name,
                "activity": activity,
                "pid": current.get("pid"),
            }
        except Exception as e:
            return {"success": False, "message": f"app_current failed: {str(e)}"}

    def wait_activity(self, activity: str, timeout: float = 10.0) -> Dict[str, Any]:
        """Wait until an Android activity is active."""
        try:
            device = self._get_u2_device()
            if not device:
                return {"success": False, "message": "uiautomator2 设备连接不可用"}

            matched = bool(device.wait_activity(activity, timeout=timeout))
            return {
                "success": matched,
                "message": (
                    f"activity matched: {activity}"
                    if matched
                    else f"activity not matched within {timeout}s: {activity}"
                ),
                "activity": activity,
                "timeout": timeout,
            }
        except Exception as e:
            return {"success": False, "message": f"wait_activity failed: {str(e)}"}

    def clear_text(self) -> Dict[str, Any]:
        """Clear focused input text."""
        try:
            device = self._get_u2_device()
            if not device:
                return {"success": False, "message": "uiautomator2 设备连接不可用"}

            device.clear_text()
            self.invalidate_ui_tree_cache()
            return {"success": True, "message": "已清空当前输入框文本"}
        except Exception as e:
            return {"success": False, "message": f"clear_text failed: {str(e)}"}

    def double_click(
        self, x: int, y: int, duration: float = 0.1
    ) -> Dict[str, Any]:
        """Double click screen coordinates."""
        try:
            device = self._get_u2_device()
            if not device:
                return {"success": False, "message": "uiautomator2 设备连接不可用"}

            device.double_click(int(x), int(y), duration=duration)
            self.invalidate_ui_tree_cache()
            return {"success": True, "message": f"已双击坐标 ({int(x)}, {int(y)})"}
        except Exception as e:
            return {"success": False, "message": f"double_click failed: {str(e)}"}

    def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        duration: float = 0.5,
    ) -> Dict[str, Any]:
        """Drag from one coordinate to another."""
        try:
            device = self._get_u2_device()
            if not device:
                return {"success": False, "message": "uiautomator2 设备连接不可用"}

            device.drag(
                int(start_x),
                int(start_y),
                int(end_x),
                int(end_y),
                duration=duration,
            )
            self.invalidate_ui_tree_cache()
            return {
                "success": True,
                "message": f"已拖拽 ({int(start_x)}, {int(start_y)}) -> ({int(end_x)}, {int(end_y)})",
            }
        except Exception as e:
            return {"success": False, "message": f"drag failed: {str(e)}"}

    def swipe_points(
        self, points: List[List[int]], duration: float = 0.5
    ) -> Dict[str, Any]:
        """Swipe through multiple points."""
        try:
            device = self._get_u2_device()
            if not device:
                return {"success": False, "message": "uiautomator2 设备连接不可用"}

            normalized_points = [
                [int(point[0]), int(point[1])] for point in points if len(point) >= 2
            ]
            if len(normalized_points) < 2:
                return {"success": False, "message": "swipe_points 至少需要 2 个坐标点"}

            device.swipe_points(normalized_points, duration=duration)
            self.invalidate_ui_tree_cache()
            return {
                "success": True,
                "message": f"已按 {len(normalized_points)} 个坐标点滑动",
                "points": normalized_points,
            }
        except Exception as e:
            return {"success": False, "message": f"swipe_points failed: {str(e)}"}

    def app_info(self, package_name: str) -> Dict[str, Any]:
        """Get installed app metadata."""
        try:
            device = self._get_u2_device()
            if not device:
                return {"success": False, "message": "uiautomator2 设备连接不可用"}

            info = device.app_info(package_name) or {}
            return {"success": True, **info}
        except Exception as e:
            return {"success": False, "message": f"app_info failed: {str(e)}"}

    def app_list(self, filter: Optional[str] = None) -> Dict[str, Any]:
        """List installed apps."""
        try:
            device = self._get_u2_device()
            if not device:
                return {"success": False, "message": "uiautomator2 设备连接不可用"}

            packages = device.app_list(filter=filter)
            return {"success": True, "packages": packages, "filter": filter}
        except Exception as e:
            return {"success": False, "message": f"app_list failed: {str(e)}"}

    def get_screen_image(self) -> Dict[str, Any]:
        try:
            screenshot = get_screenshot(self.device_id)
            return {
                "success": True,
                "image_base64": screenshot.base64_data,
                "width": screenshot.width,
                "height": screenshot.height,
                "is_sensitive": screenshot.is_sensitive,
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"get_screen_image_failed: {str(e)}",
                "image_base64": "",
                "width": 0,
                "height": 0,
                "is_sensitive": False,
            }

    def get_ui_tree(self, force_refresh: bool = False) -> UITreeResult:
        """获取 UI 元素树（优先使用 uiautomator2，降级到 uiautomator dump）"""
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

        if not force_refresh and self._cached_ui_tree:
            now = time.time()
            if now - self._cached_ui_tree_time < self._ui_tree_cache_timeout:
                self.log("使用缓存的 UI 树")
                result.elements = self._cached_ui_tree
                for i, elem in enumerate(result.elements):
                    elem.index = i
                return result

        ui_tree_from_u2 = self._get_ui_tree_via_uiautomator2()
        if ui_tree_from_u2:
            result.elements = ui_tree_from_u2
            self._cached_ui_tree = ui_tree_from_u2
            self._cached_ui_tree_time = time.time()
            self.log(f"通过 uiautomator2 获取UI树，获得 {len(result.elements)} 个元素")
            return result

        self.log("uiautomator2 获取失败，尝试使用 uiautomator dump 命令")
        xml_content = self._get_ui_tree_via_dump()
        
        result.raw_xml = xml_content

        if not xml_content:
            self.log("获取到的UI XML为空")
            return result

        if "<?xml" not in xml_content:
            self.log(f"获取到的内容不是有效的XML: {xml_content[:200]}")
            return result

        try:
            xml_start = xml_content.find("<?xml")
            if xml_start > 0:
                xml_content = xml_content[xml_start:]

            root = ET.fromstring(xml_content)
            self._parse_xml_elements(root, result.elements, depth=0)
            self._cached_ui_tree = result.elements
            self._cached_ui_tree_time = time.time()
            self.log(f"成功解析UI树，获得 {len(result.elements)} 个元素")
        except ET.ParseError as e:
            self.log(f"XML 解析错误: {e}")
            self.log(f"XML 内容(前500字符): {xml_content[:500]}")
        except Exception as e:
            self.log(f"处理UI树时发生未知错误: {str(e)}")
            import traceback
            self.log(traceback.format_exc())

        for i, elem in enumerate(result.elements):
            elem.index = i

        return result

    def invalidate_ui_tree_cache(self) -> None:
        """使 UI 树缓存失效（在页面变化后调用）"""
        self._cached_ui_tree = None
        self._cached_ui_tree_time = 0

    def _get_ui_tree_via_uiautomator2(self) -> Optional[List[UIElement]]:
        """使用 uiautomator2 库获取 UI 树（复用连接）"""
        device = self._get_u2_device()
        if not device:
            return None

        try:
            self.log("开始获取 UI 层次结构...")

            try:
                hierarchy = device.dump_hierarchy(compressed=True)
            except TypeError:
                hierarchy = device.dump_hierarchy()
            
            if not hierarchy:
                self.log("uiautomator2 返回的内容为空")
                return None
            
            if "<?xml" not in hierarchy[:100]:
                self.log(f"uiautomator2 返回的内容不是有效的XML: {hierarchy[:200]}")
                return None
            
            self.log(f"成功获取 UI XML，长度: {len(hierarchy)} 字符")

            root = ET.fromstring(hierarchy)
            elements = []
            self._parse_xml_elements(root, elements, depth=0)
            
            for i, elem in enumerate(elements):
                elem.index = i
            
            self.log(f"通过 uiautomator2 成功解析 {len(elements)} 个元素")
            return elements
            
        except Exception as e:
            self.log(f"通过 uiautomator2 获取UI树失败: {str(e)}")
            self._u2_device = None
            return None

    def _get_ui_tree_via_dump(self) -> str:
        """使用 uiautomator dump 命令获取 UI XML"""
        xml_content = ""
        
        dump_paths = [
            "/data/local/tmp/ui_dump.xml",
            "/sdcard/ui_dump.xml",
            "/storage/emulated/0/ui_dump.xml",
            "/data/data/com.android.uiautomator/ui_dump.xml",
        ]

        for dump_path in dump_paths:
            try:
                self._run_adb_shell(f"rm -f {dump_path} 2>/dev/null || true")
                dump_output = self._run_adb_shell(f"uiautomator dump {dump_path}")
                self.log(f"uiautomator dump 输出: {dump_output}")

                if (
                    "UI hierchary dumped to" in dump_output
                    or "ERROR" not in dump_output
                ):
                    xml_content = self._run_adb_shell(f"cat {dump_path}")
                    if xml_content and "<?xml" in xml_content[:50]:
                        self.log(f"成功从 {dump_path} 获取UI XML")
                        break

                self._run_adb_shell(f"rm -f {dump_path} 2>/dev/null || true")

            except Exception as e:
                self.log(f"尝试路径 {dump_path} 时发生异常: {str(e)}")
                continue

        if not xml_content or "<?xml" not in xml_content[:50]:
            self.log("未能获取有效的UI XML")

        return xml_content

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
            elif (
                by in ("textContains", "textContain")
                and value.lower() in elem.text.lower()
            ):
                results.append(elem)
            elif by == "resource-id" and elem.resource_id == value:
                results.append(elem)
            elif by in ("resourceId", "resource_id") and elem.resource_id == value:
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

    def _click_element_via_uiautomator2(
        self, by: str, value: str
    ) -> Optional[Dict[str, Any]]:
        if by not in {
            "text",
            "textContains",
            "textContain",
            "resource-id",
            "resourceId",
            "resource_id",
            "content-desc",
            "contentDescription",
        }:
            return None

        device = self._get_u2_device()
        if not device:
            return None

        try:
            selectors: List[Dict[str, str]] = []

            if by == "text":
                selectors.append({"text": value})
                selectors.append({"textContains": value})
            elif by in ("textContains", "textContain"):
                selectors.append({"textContains": value})
            elif by in ("resource-id", "resourceId", "resource_id"):
                selectors.append({"resourceId": value})
            elif by in ("content-desc", "contentDescription"):
                selectors.append({"description": value})

            for selector in selectors:
                obj = device(**selector)
                if obj.exists:
                    obj.click()
                    self.invalidate_ui_tree_cache()
                    return {
                        "success": True,
                        "message": f"已通过 uiautomator2 点击元素: {by}={value}",
                        "strategy": "uiautomator2",
                        "selector": selector,
                        "clicked": True,
                    }

            return {
                "success": False,
                "message": f"uiautomator2 未找到元素: {by}={value}",
                "strategy": "uiautomator2",
                "clicked": False,
                "attempted_selectors": selectors,
            }
        except Exception as exc:
            self._u2_device = None
            return {
                "success": False,
                "message": f"uiautomator2 点击失败: {exc}",
                "strategy": "uiautomator2",
                "clicked": False,
            }

    def click_element(self, by: str, value: str) -> Dict[str, Any]:
        """点击语义元素；bounds 作为已定位元素的兼容入口。"""
        if by == "bounds":
            try:
                bounds = self._parse_bounds(value)
            except (TypeError, ValueError):
                return {"success": False, "message": f"无效 bounds: {value}"}

            if bounds["right"] <= bounds["left"] or bounds["bottom"] <= bounds["top"]:
                return {"success": False, "message": f"无效 bounds: {value}"}

            x = (bounds["left"] + bounds["right"]) // 2
            y = (bounds["top"] + bounds["bottom"]) // 2
            tap_result = self.tap(x, y)
            if tap_result.get("success"):
                tap_result["bounds"] = bounds
            return tap_result

        direct_result = self._click_element_via_uiautomator2(by, value)
        if direct_result and direct_result.get("success"):
            return direct_result

        if direct_result:
            return direct_result
        return {"success": False, "message": f"未找到元素: {by}={value}"}

    def click_position(self, x: int, y: int) -> Dict[str, Any]:
        """点击指定坐标位置"""
        try:
            device = self._get_u2_device()
            if device:
                try:
                    device.click(x, y)
                    self.log(f"通过 uiautomator2 点击坐标 ({x}, {y})")
                    self.invalidate_ui_tree_cache()
                    return {"success": True, "message": f"已点击坐标 ({x}, {y})"}
                except Exception as e:
                    self.log(f"uiautomator2 点击失败: {str(e)}")
                    self._u2_device = None
            
            output = self._run_adb_shell(f"input tap {x} {y}")
            if not output:
                self.invalidate_ui_tree_cache()
                return {"success": True, "message": f"已点击坐标 ({x}, {y})"}
            return {"success": False, "message": f"点击失败: {output}"}
        except Exception as e:
            return {"success": False, "message": f"点击坐标失败: {str(e)}"}

    def tap(self, x: int, y: int) -> Dict[str, Any]:
        """按坐标点击屏幕。"""
        output = self._run_adb_shell(f"input tap {int(x)} {int(y)}")

        if not output:
            return {"success": True, "message": f"已点击坐标 ({int(x)}, {int(y)})"}

        return {"success": False, "message": f"点击失败: {output}"}

    def input_text(self, text: str, clear_first: bool = True) -> Dict[str, Any]:
        """输入文本（支持安全键盘场景）"""
        try:
            # 方法1：优先使用 uiautomator2 的 send_keys（对安全键盘最有效）
            result = self._input_text_uiautomator2(text, clear_first)
            if result.get("success"):
                return result
            self.log(f"uiautomator2 方法失败: {result.get('message')}")
        except Exception as e:
            self.log(f"uiautomator2 方法异常: {e}")
        
        try:
            # 方法2：尝试使用 ADB Keyboard 方法（绕过安全键盘限制）
            return self._input_text_adb_keyboard(text, clear_first)
        except Exception as e:
            self.log(f"ADB Keyboard 方法失败: {e}")
            
        # 方法3：回退到标准方法
        return self._input_text_standard(text, clear_first)

    def _input_text_uiautomator2(self, text: str, clear_first: bool = True) -> Dict[str, Any]:
        """使用 uiautomator2 输入文本（对安全键盘最有效，复用连接）"""
        device = self._get_u2_device()
        if not device:
            return {"success": False, "message": "uiautomator2 设备连接不可用"}

        try:
            focused_elem = device(focused=True)
            
            if focused_elem.exists:
                self.log(f"找到焦点元素，使用 set_text 输入")
                if clear_first:
                    focused_elem.clear_text()
                    time.sleep(0.1)
                
                try:
                    focused_elem.set_text(text)
                    time.sleep(0.2)
                    current_text = focused_elem.get_text()
                    if current_text or len(text) == 0:
                        return {"success": True, "message": f"已通过 uiautomator2 set_text 输入文本"}
                except Exception as e:
                    self.log(f"set_text 失败: {e}")
                
                self.log("尝试剪贴板方式输入")
                return self._input_text_clipboard(device, text, clear_first)
            else:
                self.log(f"未找到焦点元素，使用 send_keys 输入")
                if clear_first:
                    device.send_keys("")
                    time.sleep(0.1)
                device.send_keys(text)
                return {"success": True, "message": f"已通过 uiautomator2 send_keys 输入文本"}
                
        except Exception as e:
            self._u2_device = None
            return {"success": False, "message": f"uiautomator2 输入失败: {str(e)}"}
    
    def _input_text_clipboard(self, device, text: str, clear_first: bool = True) -> Dict[str, Any]:
        """使用剪贴板方式输入文本（绕过安全键盘限制）"""
        try:
            import subprocess
            
            # 将文本复制到设备剪贴板
            # 使用 am broadcast 发送剪贴板内容
            encoded_text = text.replace("'", "\\'")
            
            # 方法1：使用 service call clipboard
            cmd = self._get_adb_prefix() + [
                "shell", "service", "call", "clipboard", "2",
                "i32", "1", "i32", str(len(text)), "s16", text
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            
            if result.returncode == 0:
                self.log("已将文本复制到剪贴板")
                
                # 然后模拟粘贴操作 (Ctrl+V 或长按粘贴)
                if clear_first:
                    # 全选
                    device.press("ctrl+a")
                    time.sleep(0.1)
                
                # 粘贴
                device.press("ctrl+v")
                time.sleep(0.2)
                
                return {"success": True, "message": f"已通过剪贴板输入文本"}
            else:
                # 方法2：使用 am broadcast 设置剪贴板
                cmd = self._get_adb_prefix() + [
                    "shell", "am", "broadcast", 
                    "-a", "com.android.intent.action.SET_CLIPBOARD",
                    "--es", "text", text
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
                
                if result.returncode == 0:
                    device.press("ctrl+v")
                    return {"success": True, "message": f"已通过剪贴板输入文本"}
                    
        except Exception as e:
            return {"success": False, "message": f"剪贴板输入失败: {str(e)}"}

    def _input_text_adb_keyboard(self, text: str, clear_first: bool = True) -> Dict[str, Any]:
        """使用 ADB Keyboard 输入文本（绕过安全键盘限制）"""
        import base64
        
        if clear_first:
            # 发送清除文本广播
            cmd = self._get_adb_prefix() + [
                "shell", "am", "broadcast", "-a", "ADB_CLEAR_TEXT"
            ]
            subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            time.sleep(0.2)
        
        # 使用 base64 编码发送文本
        encoded_text = base64.b64encode(text.encode("utf-8")).decode("utf-8")
        cmd = self._get_adb_prefix() + [
            "shell", "am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", encoded_text
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        
        if result.returncode == 0:
            return {"success": True, "message": f"已输入文本: {text}"}
        else:
            raise Exception(f"ADB Keyboard input failed: {result.stderr}")

    def _input_text_standard(self, text: str, clear_first: bool = True) -> Dict[str, Any]:
        """标准输入文本方法"""
        if clear_first:
            self._run_adb_shell("input keyevent KEYCODE_CTRL_A")
            time.sleep(0.1)

        text = text.replace(" ", "%s")
        output = self._run_adb_shell(f"input text {text}")

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

        output = self._run_adb_shell(f"input swipe {start_x} {start_y} {end_x} {end_y}")

        if not output:
            return {"success": True, "message": f"已滑动 {direction}"}

        return {"success": False, "message": f"滑动失败: {output}"}

    def long_press(self, by: str, value: str, duration: int = 1000) -> Dict[str, Any]:
        """长按操作"""
        element = self.find_element(by, value, timeout=2)

        if not element:
            return {"success": False, "message": f"未找到元素: {by}={value}"}

        x, y = element.get_center()
        output = self._run_adb_shell(f"input touchscreen longpress {x} {y} {duration}")

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

    def _find_top_left_back_control(self) -> Optional[tuple[str, str]]:
        """优先寻找页面左上角的返回/关闭控件。"""
        try:
            ui_tree = self.get_ui_tree()
        except Exception:
            return None

        elements = getattr(ui_tree, "elements", []) or []
        if not elements:
            return None

        labels = {
            "返回",
            "返回上一页",
            "后退",
            "关闭",
            "Back",
            "Navigate up",
            "Close",
        }
        keyword_labels = ("back", "navigate", "close", "up")
        candidates: List[tuple[int, UIElement]] = []

        for elem in elements:
            if not getattr(elem, "enabled", True):
                continue

            bounds = getattr(elem, "bounds", {}) or {}
            left = int(bounds.get("left", 0) or 0)
            top = int(bounds.get("top", 0) or 0)
            right = int(bounds.get("right", 0) or 0)
            bottom = int(bounds.get("bottom", 0) or 0)
            if right <= left or bottom <= top:
                continue

            center_x = (left + right) // 2
            center_y = (top + bottom) // 2
            if center_x > 180 or center_y > 180:
                continue

            text = (getattr(elem, "text", "") or "").strip()
            content_desc = (getattr(elem, "content_desc", "") or "").strip()
            resource_id = (getattr(elem, "resource_id", "") or "").strip().lower()
            class_name = (getattr(elem, "class_name", "") or "").strip().lower()

            score = 0
            if content_desc in labels:
                score += 100
            if text in labels:
                score += 90
            lowered = f"{content_desc} {text} {resource_id}".lower()
            if any(keyword in lowered for keyword in keyword_labels):
                score += 50
            if "imagebutton" in class_name or "button" in class_name:
                score += 10
            if getattr(elem, "clickable", False):
                score += 5

            if score:
                candidates.append((score - center_x - center_y, elem))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        elem = candidates[0][1]
        if getattr(elem, "content_desc", ""):
            return "content-desc", elem.content_desc
        if getattr(elem, "text", ""):
            return "text", elem.text

        bounds = elem.bounds
        return "bounds", f"{bounds['left']},{bounds['top']},{bounds['right']},{bounds['bottom']}"

    def back(self) -> Dict[str, Any]:
        """返回操作"""
        back_control = self._find_top_left_back_control()
        if back_control:
            by, value = back_control
            result = self.click_element(by, value)
            if isinstance(result, dict) and result.get("success"):
                result.setdefault("message", "已点击页面返回控件")
                result["back_strategy"] = "ui_control"
                return result

        output = self._run_adb_shell("input keyevent KEYCODE_BACK")

        if not output:
            return {
                "success": True,
                "message": "已执行系统返回",
                "back_strategy": "system_key",
            }

        return {"success": False, "message": f"返回失败: {output}"}

    def home(self) -> Dict[str, Any]:
        """回到桌面"""
        output = self._run_adb_shell("input keyevent KEYCODE_HOME")

        if not output:
            return {"success": True, "message": "已回到桌面"}

        return {"success": False, "message": f"回到桌面失败: {output}"}

    def press_key(self, key_code: str) -> Dict[str, Any]:
        """按键操作"""
        key_map = {
            "BACK": "KEYCODE_BACK",
            "HOME": "KEYCODE_HOME",
            "ENTER": "KEYCODE_ENTER",
            "DEL": "KEYCODE_DEL",
            "VOLUME_UP": "KEYCODE_VOLUME_UP",
            "VOLUME_DOWN": "KEYCODE_VOLUME_DOWN",
            "POWER": "KEYCODE_POWER",
            "MENU": "KEYCODE_MENU",
            "SEARCH": "KEYCODE_SEARCH",
        }

        # 如果已经是完整的 keycode，直接使用
        if key_code.startswith("KEYCODE_"):
            adb_key_code = key_code
        else:
            adb_key_code = key_map.get(key_code, f"KEYCODE_{key_code}")

        output = self._run_adb_shell(f"input keyevent {adb_key_code}")

        if not output:
            return {"success": True, "message": f"已执行按键: {key_code}"}

        return {"success": False, "message": f"按键失败: {output}"}

    def close_app(
        self, app_name: str, package_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """关闭应用（强制停止应用进程）"""
        try:
            # 如果没有提供 package_name，尝试从 app_name 映射
            if not package_name:
                # 先尝试获取包名映射
                mapped_package = get_package_name(app_name)
                if mapped_package:
                    package_name = mapped_package
                    self.log(f"应用名称 '{app_name}' 映射到包名: {package_name}")

            # 使用 am force-stop 命令强制停止应用
            target_package = package_name or app_name
            output = self._run_adb_shell(f"am force-stop {target_package}")

            # am force-stop 命令通常没有输出，没有错误就是成功
            success = not output or "error" not in output.lower()

            if success:
                self.log(f"应用 '{app_name}' 已成功关闭")
                return {
                    "success": True,
                    "message": f"已关闭应用: {app_name}",
                    "app_name": app_name,
                    "package_name": target_package,
                }
            else:
                self.log(f"关闭应用失败，输出: {output}")
                return {
                    "success": False,
                    "message": f"无法关闭应用 '{app_name}'",
                    "debug_output": output,
                }
        except Exception as e:
            self.log(f"关闭应用时发生异常: {e}")
            return {"success": False, "message": f"关闭应用失败: {str(e)}"}

    def install_apk(self, file_path: str) -> Dict[str, Any]:
        """安装APK"""
        # 大文件 push + pm install 耗时较长，使用 300s 超时避免误判失败
        output = self._run_adb_command(f"install -r {file_path}", timeout=300)

        if "Success" in output:
            return {"success": True, "message": "安装成功"}
        elif "already installed" in output:
            return {"success": True, "message": "应用已安装"}

        return {"success": False, "message": f"安装失败: {output}"}

    def uninstall_apk(self, package_name: str) -> Dict[str, Any]:
        """卸载APK"""
        # 大包卸载可能较慢，放宽到 120s
        output = self._run_adb_command(f"uninstall {package_name}", timeout=120)

        if "Success" in output:
            return {"success": True, "message": "卸载成功"}
        elif "not installed" in output:
            return {"success": True, "message": "应用未安装"}

        return {"success": False, "message": f"卸载失败: {output}"}

    def swipe_by_coords(
        self, start_x: int, start_y: int, end_x: int, end_y: int
    ) -> Dict[str, Any]:
        """按坐标滑动操作"""
        output = self._run_adb_shell(f"input swipe {start_x} {start_y} {end_x} {end_y}")

        if not output:
            return {
                "success": True,
                "message": f"已从 ({start_x}, {start_y}) 滑动到 ({end_x}, {end_y})",
            }

        return {"success": False, "message": f"滑动失败: {output}"}

    def launch_app(
        self, app_name: str, package_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """启动应用"""
        try:
            # 如果没有提供 package_name，尝试从 app_name 映射
            if not package_name:
                # 先尝试获取包名映射
                mapped_package = get_package_name(app_name)
                if mapped_package:
                    package_name = mapped_package
                    self.log(f"应用名称 '{app_name}' 映射到包名: {package_name}")

            # 现在有多种方式来启动应用，按优先级尝试
            launch_methods = []

            if package_name:
                # 方法1: 使用 monkey 命令启动（最可靠，不依赖特定Activity名称）
                launch_methods.append(
                    (
                        "monkey",
                        f"monkey -p {package_name} -c android.intent.category.LAUNCHER 1",
                    )
                )

                # 方法2: 使用 am start 启动 Launcher Activity（通用）
                launch_methods.append(
                    (
                        "am start (Launcher)",
                        f"am start -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -n {package_name}",
                    )
                )

                # 方法3: 尝试常见的启动Activity名称
                common_activities = [
                    ".MainActivity",
                    ".SplashActivity",
                    ".ui.activity.SplashActivity",
                    ".splash.SplashActivity",
                    ".activity.SplashActivity",
                    ".ui.SplashActivity",
                    ".activity.MainActivity",
                    ".ui.activity.MainActivity",
                ]
                for activity in common_activities:
                    launch_methods.append(
                        (
                            f"am start {activity}",
                            f"am start -n {package_name}{activity}",
                        )
                    )
            else:
                # 没有包名的情况下，直接尝试用 app_name 作为包名（不太可靠）
                launch_methods.append(
                    (
                        "monkey (direct)",
                        f"monkey -p {app_name} -c android.intent.category.LAUNCHER 1",
                    )
                )

            # 尝试所有启动方法
            last_output = ""
            success = False
            used_method = ""
            for method_name, command in launch_methods:
                self.log(f"尝试使用 '{method_name}' 启动应用...")
                output = self._run_adb_shell(command)
                last_output = output
                self.log(f"'{method_name}' 输出: {output}")

                # 判断是否成功
                success_indicators = [
                    "Starting",
                    "activity started",
                    "Events injected",
                    "No arg",
                    "monkey aborted",  # monkey有时会输出这个但实际启动成功了
                ]

                # 检查是否有成功的标志
                is_success = False
                for indicator in success_indicators:
                    if indicator.lower() in output.lower():
                        is_success = True
                        break

                # 如果没有输出，也可能是成功
                if not output or is_success:
                    success = True
                    used_method = method_name
                    self.log(f"应用 '{app_name}' 启动成功 (使用 {method_name})")
                    break

            if success:
                actual_package = package_name or app_name
                # 等待一下让应用完全启动
                time.sleep(0.5)
                # 获取当前应用信息
                current_app_info = self.get_current_app()
                current_package = ""
                if isinstance(current_app_info, dict):
                    current_package = str(current_app_info.get("current_app", "") or "")

                if package_name and current_package and current_package != package_name:
                    self.log(
                        "应用启动命令返回成功，但当前前台应用不匹配: "
                        f"expected={package_name}, actual={current_package}"
                    )
                    return {
                        "success": False,
                        "error": f"启动后前台应用仍是 {current_package}",
                        "message": (
                            f"当前前台应用仍是 {current_package}，"
                            f"未成功进入目标应用 {app_name}"
                        ),
                        "app_name": app_name,
                        "expected_package": package_name,
                        "actual_current_app": current_package,
                        "current_app": current_app_info,
                        "launch_method": used_method,
                        "debug_output": last_output,
                    }

                return {
                    "success": True,
                    "message": f"已启动应用: {app_name}",
                    "app_name": app_name,
                    "package_name": actual_package,
                    "current_app": current_app_info,
                    "launch_method": used_method,
                    "debug_output": last_output,
                }

            # 所有方法都失败了
            self.log(f"所有启动方法都失败，最后输出: {last_output}")
            return {
                "success": False,
                "error": f"启动失败: 无法启动应用 '{app_name}'",
                "message": f"应用 '{app_name}' 启动失败，请确保：\n1. 应用已安装\n2. 包名正确\n3. 有启动权限\n\n调试信息: {last_output[:200]}",
                "details": last_output,
                "suggestion": "尝试使用包名直接启动，或检查应用是否已安装",
            }

        except Exception as e:
            import traceback

            error_detail = traceback.format_exc()
            return {
                "success": False,
                "error": f"启动应用异常: {str(e)}",
                "message": f"启动应用 '{app_name}' 时发生异常: {str(e)}",
                "details": error_detail,
            }

    # ============ 设备管理方法 ============

    def discover_devices(self) -> List[DeviceInfo]:
        """发现可用设备"""
        cmd = ["adb", "devices", "-l"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        output = result.stdout.strip() if result.stdout else ""

        devices = []
        usb_devices = []
        wifi_devices = []

        for line in output.split("\n"):
            line = line.strip()
            if line and "List of devices attached" not in line:
                parts = line.split()
                if len(parts) >= 2:
                    device_id = parts[0]
                    status = parts[1]

                    device_info = DeviceInfo(id=device_id, status=status)

                    for part in parts[2:]:
                        if part.startswith("model:"):
                            device_info.model = part[6:]
                        elif part.startswith("device:"):
                            device_info.name = part[7:]

                    if ":" in device_id:
                        device_info.connection_type = "wifi"
                        device_info.ip = device_id.split(":")[0]
                        device_info.port = device_id.split(":")[1]
                        wifi_devices.append(device_info)
                    else:
                        device_info.connection_type = "usb"
                        if status == "device":
                            device_info.brand = self._get_device_prop(
                                device_id,
                                "ro.product.brand",
                            )
                            product_model = self._get_device_prop(
                                device_id,
                                "ro.product.model",
                            )
                            if product_model:
                                device_info.model = product_model
                        usb_devices.append(device_info)

        devices = usb_devices + wifi_devices
        return devices

    def _get_device_wifi_ip(self, device_id: str) -> Optional[str]:
        """获取USB连接设备的WiFi IP地址"""
        cmd = ["adb", "-s", device_id, "shell", "ip", "route"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )

        if result.stdout:
            for line in result.stdout.split("\n"):
                line = line.strip()
                if line and "wlan" in line.lower():
                    parts = line.split()
                    if len(parts) >= 9:
                        return parts[8]

            cmd2 = ["adb", "-s", device_id, "shell", "ip", "addr", "show", "wlan0"]
            result2 = subprocess.run(
                cmd2,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            if result2.stdout:
                for line in result2.stdout.split("\n"):
                    line = line.strip()
                    if line.startswith("inet ") and not line.startswith("inet 127."):
                        ip = line.split()[1].split("/")[0]
                        return ip
        return None

    def connect_wireless_device(
        self, ip: str, port: str = "5555", usb_device_id: str = None
    ) -> Dict[str, Any]:
        """连接WiFi设备"""
        if usb_device_id:
            cmd = ["adb", "-s", usb_device_id, "tcpip", port]
            subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)

        cmd = ["adb", "connect", f"{ip}:{port}"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

        output = result.stdout.strip() if result.stdout else ""
        success = "connected" in output.lower()

        return {
            "success": success,
            "message": output,
            "device_id": f"{ip}:{port}" if success else None,
        }

    def disconnect_device(self, device_id: str) -> Dict[str, Any]:
        """断开设备连接"""
        cmd = ["adb", "disconnect", device_id]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

        output = result.stdout.strip() if result.stdout else ""
        success = "disconnected" in output.lower() or "no device" in output.lower()
        return {"success": success, "message": output}

    def disconnect_all_wireless_devices(self) -> Dict[str, bool]:
        """断开所有WiFi设备连接"""
        cmd = ["adb", "disconnect"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

        output = result.stdout.strip() if result.stdout else ""
        success = "disconnected" in output.lower() or "no device" in output.lower()
        return {"success": success}

    def get_device_info(self, device_id: str) -> Optional[DeviceInfo]:
        """获取设备信息"""
        devices = self.discover_devices()
        for device in devices:
            if device.id == device_id:
                cmd = [
                    "adb",
                    "-s",
                    device_id,
                    "shell",
                    "getprop",
                    "ro.build.version.release",
                ]
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                )
                device.version = result.stdout.strip() if result.stdout else ""
                
                # 如果是USB连接的设备且没有WiFi IP，尝试获取
                if device.connection_type == "usb" and not device.wifi_ip:
                    wifi_ip = self._get_device_wifi_ip(device_id)
                    if wifi_ip:
                        device.wifi_ip = wifi_ip
                        device.wifi_enabled = True
                
                return device
        return None

    # ============ 设备管理方法结束 ============

    def wait(self, duration: float = 2.0) -> Dict[str, Any]:
        """等待"""
        try:
            duration_seconds = float(duration)
        except (TypeError, ValueError):
            return {"success": False, "message": f"无效等待时长: {duration}"}
        time.sleep(duration_seconds)
        return {"success": True, "message": f"已等待 {duration_seconds:g} 秒"}

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
                                    "bounds",
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
                    "description": "点击自然语言可定位的 UI 元素。用于“点击某某按钮/文本”等语义点击；不要用于纯坐标点击",
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
                    "name": "tap",
                    "description": "按屏幕坐标点击。仅当已经明确知道坐标时使用；点击可见文本或按钮时优先使用 click_element",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "integer", "description": "屏幕横坐标"},
                            "y": {"type": "integer", "description": "屏幕纵坐标"},
                        },
                        "required": ["x", "y"],
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
                    "name": "back",
                    "description": "执行返回操作",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "home",
                    "description": "回到桌面",
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
                            "app_name": {
                                "type": "string",
                                "description": "应用名称或包名",
                            },
                            "package_name": {
                                "type": "string",
                                "description": "应用包名（可选）",
                            },
                        },
                        "required": ["app_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "close_app",
                    "description": "关闭指定应用（强制停止应用进程）",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "app_name": {
                                "type": "string",
                                "description": "应用名称或包名",
                            },
                            "package_name": {
                                "type": "string",
                                "description": "应用包名（可选）",
                            },
                        },
                        "required": ["app_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_current_app",
                    "description": "获取当前前台应用信息",
                    "parameters": {"type": "object", "properties": {}, "required": []},
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
            {
                "type": "function",
                "function": {
                    "name": "get_screen_info",
                    "description": "获取屏幕尺寸和方向信息，用于计算坐标、滑动距离和横竖屏判断",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "app_current",
                    "description": "通过 uiautomator2 获取当前前台应用和 Activity 信息",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "wait_activity",
                    "description": "等待指定 Activity 出现在前台，适合启动或页面跳转后替代固定等待",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "activity": {
                                "type": "string",
                                "description": "目标 Activity 名称，可为完整名或短名",
                            },
                            "timeout": {
                                "type": "number",
                                "description": "最长等待时间（秒）",
                                "default": 10.0,
                            },
                        },
                        "required": ["activity"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "clear_text",
                    "description": "清空当前聚焦输入框中的文本",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "double_click",
                    "description": "按屏幕坐标执行双击操作",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "integer", "description": "屏幕横坐标"},
                            "y": {"type": "integer", "description": "屏幕纵坐标"},
                            "duration": {
                                "type": "number",
                                "description": "两次点击之间的间隔秒数",
                                "default": 0.1,
                            },
                        },
                        "required": ["x", "y"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "drag",
                    "description": "按坐标拖拽控件，适合滑块、排序、拖动元素等场景",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "start_x": {"type": "integer", "description": "起点横坐标"},
                            "start_y": {"type": "integer", "description": "起点纵坐标"},
                            "end_x": {"type": "integer", "description": "终点横坐标"},
                            "end_y": {"type": "integer", "description": "终点纵坐标"},
                            "duration": {
                                "type": "number",
                                "description": "拖拽持续时间（秒）",
                                "default": 0.5,
                            },
                        },
                        "required": ["start_x", "start_y", "end_x", "end_y"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "swipe_points",
                    "description": "按多个坐标点执行连续轨迹滑动，适合复杂手势",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "points": {
                                "type": "array",
                                "description": "坐标点数组，如 [[200, 300], [210, 320]]",
                                "items": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "minItems": 2,
                                },
                                "minItems": 2,
                            },
                            "duration": {
                                "type": "number",
                                "description": "点之间的注入间隔/持续参数",
                                "default": 0.5,
                            },
                        },
                        "required": ["points"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "app_info",
                    "description": "获取指定应用的包信息、版本等元数据",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "package_name": {
                                "type": "string",
                                "description": "应用包名",
                            }
                        },
                        "required": ["package_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "app_list",
                    "description": "查询设备上的应用包名列表，可按 uiautomator2 支持的 filter 过滤",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "filter": {
                                "type": "string",
                                "description": "过滤条件，如 -3、-s、third-party 等；为空则返回全部",
                            }
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finish",
                    "description": "完成任务，返回结果",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "message": {
                                "type": "string",
                                "description": "任务完成信息",
                            },
                            "success": {
                                "type": "boolean",
                                "description": "是否成功完成",
                                "default": True,
                            },
                        },
                        "required": ["message"],
                    },
                },
            },
        ]
