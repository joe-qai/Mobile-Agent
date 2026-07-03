"""MCP工具模块 - 新版ADB工具的兼容层

本文件作为过渡层，保持旧版API接口，但内部委托给新版ADBMCTools实现。
重构完成后可删除此文件。
"""

from typing import Any, Dict, List, Optional

try:
    from backend.mcp.mcp_tools_adb import ADBMCTools, DeviceInfo
except ImportError:
    from web_ui.backend.mcp.mcp_tools_adb import ADBMCTools, DeviceInfo


class MCPTools:
    """MCP工具类 - 兼容层，委托给ADBMCTools"""

    def __init__(self):
        self.current_device = None
        self.current_platform = "android"
        self._adb_tools = ADBMCTools()
        # 设备列表缓存
        self._devices_cache = None
        self._devices_cache_time = 0
        self._devices_cache_ttl = 10  # 缓存有效期10秒

    def _get_tools(self, device_id: str = None) -> ADBMCTools:
        """获取ADB工具实例"""
        target_device = device_id or self.current_device
        return ADBMCTools(device_id=target_device)

    def run_adb_command(self, command: str, device_id: str = None) -> str:
        """运行ADB命令"""
        tools = self._get_tools(device_id)
        return tools._run_adb_command(command)

    def discover_devices(self) -> List[DeviceInfo]:
        """发现可用设备（带缓存）"""
        import time
        now = int(time.time())
        
        # 检查缓存是否有效
        if self._devices_cache and (now - self._devices_cache_time) < self._devices_cache_ttl:
            return self._devices_cache
        
        # 缓存无效，重新获取
        devices = self._adb_tools.discover_devices()
        self._devices_cache = devices
        self._devices_cache_time = now
        return devices

    def invalidate_devices_cache(self):
        """使设备列表缓存失效"""
        self._devices_cache = None
        self._devices_cache_time = 0

    def connect_wireless_device(self, ip: str, port: str = "5555", usb_device_id: str = None) -> Dict[str, Any]:
        """连接WiFi设备"""
        return self._adb_tools.connect_wireless_device(ip, port, usb_device_id)

    def disconnect_device(self, device_id: str) -> Dict[str, Any]:
        """断开设备连接"""
        return self._adb_tools.disconnect_device(device_id)

    def disconnect_all_wireless_devices(self) -> Dict[str, bool]:
        """断开所有WiFi设备连接"""
        return self._adb_tools.disconnect_all_wireless_devices()

    def get_device_wifi_ip(self, device_id: str) -> Optional[str]:
        """获取USB连接设备的WiFi IP地址"""
        tools = self._get_tools(device_id)
        return tools._get_device_wifi_ip(device_id)

    def get_device_info(self, device_id: str) -> Optional[DeviceInfo]:
        """获取设备信息"""
        return self._adb_tools.get_device_info(device_id)

    def select_device(self, device_id: str) -> Dict[str, bool]:
        """选择当前设备"""
        self.current_device = device_id
        self.current_platform = "android"
        
        tools = self._get_tools(device_id)
        if tools:
            output = tools._run_adb_shell("dumpsys power | grep mScreenOn")
            if output and "false" in output.lower():
                tools._run_adb_shell("input keyevent KEYCODE_WAKEUP")
                tools._run_adb_shell("wm dismiss-keyguard")
        
        return {"success": True, "platform": self.current_platform}

    def get_screen_elements(self, device_id: str = None) -> List:
        """获取UI元素树"""
        tools = self._get_tools(device_id)
        ui_tree = tools.get_ui_tree()
        return ui_tree.elements if ui_tree else []

    def get_screen_image(self, device_id: str = None) -> Dict[str, str]:
        """获取屏幕截图"""
        tools = self._get_tools(device_id)
        return tools.get_screen_image()

    def click(self, x: int, y: int, device_id: str = None) -> Dict[str, bool]:
        """点击操作"""
        tools = self._get_tools(device_id)
        output = tools._run_adb_shell(f"input tap {x} {y}")
        return {"success": output == "", "message": output or f"已点击坐标 ({x}, {y})"}

    def long_click(self, x: int, y: int, duration: int = 1000, device_id: str = None) -> Dict[str, bool]:
        """长按操作"""
        tools = self._get_tools(device_id)
        output = tools._run_adb_shell(f"input swipe {x} {y} {x} {y} {duration}")
        return {"success": output == "", "message": output or f"已长按坐标 ({x}, {y})"}

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, device_id: str = None) -> Dict[str, bool]:
        """滑动操作（坐标方式）"""
        tools = self._get_tools(device_id)
        return tools.swipe_by_coords(start_x, start_y, end_x, end_y)

    def input_text(self, text: str, device_id: str = None) -> Dict[str, bool]:
        """输入文本"""
        tools = self._get_tools(device_id)
        return tools.input_text(text)

    def press_key(self, key: str, device_id: str = None) -> Dict[str, bool]:
        """按键操作"""
        tools = self._get_tools(device_id)
        key_map = {
            "BACK": "BACK",
            "HOME": "HOME",
            "ENTER": "ENTER",
            "DEL": "DEL",
            "VOLUME_UP": "VOLUME_UP",
            "VOLUME_DOWN": "VOLUME_DOWN",
            "POWER": "POWER",
            "MENU": "MENU",
            "SEARCH": "SEARCH",
            "back": "BACK",
            "home": "HOME",
        }
        key_code = key_map.get(key.upper(), key)
        return tools.press_key(key_code)

    def launch_app(self, package_name: str, device_id: str = None) -> Dict[str, bool]:
        """启动应用"""
        tools = self._get_tools(device_id)
        result = tools.launch_app(package_name)
        return {"success": result.get("success", False), "message": result.get("message", "")}

    def close_app(self, app_name: str, package_name: str = None, device_id: str = None) -> Dict[str, bool]:
        """关闭应用"""
        tools = self._get_tools(device_id)
        result = tools.close_app(app_name, package_name)
        return {"success": result.get("success", False), "message": result.get("message", "")}

    def install_apk(self, file_path: str, device_id: str = None) -> Dict[str, Any]:
        """安装APK"""
        tools = self._get_tools(device_id)
        return tools.install_apk(file_path)

    def uninstall_apk(self, package_name: str, device_id: str = None) -> Dict[str, Any]:
        """卸载APK"""
        tools = self._get_tools(device_id)
        if hasattr(tools, 'uninstall_apk'):
            return tools.uninstall_apk(package_name)
        return {"success": False, "message": "Uninstall not supported"}

    def get_current_app(self, device_id: str = None) -> Dict[str, str]:
        """获取当前应用"""
        tools = self._get_tools(device_id)
        return tools.get_current_app()

    def get_tool_descriptions(self) -> List[Dict[str, Any]]:
        """获取工具描述"""
        return self._adb_tools.get_tool_definitions()

    def execute_tool(self, tool_name: str, parameters: Dict[str, Any]) -> Any:
        """执行工具"""
        tools = self._get_tools(self.current_device)
        return tools.execute_tool(tool_name, parameters)


# 创建全局单例
mcp_tools = MCPTools()
