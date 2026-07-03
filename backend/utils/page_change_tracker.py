"""页面变化追踪器 - 用于记录Agent执行时的页面hash变化

通过感知哈希（pHash）检测页面变化，为脚本生成时的截图埋点提供依据。
"""

import base64
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PageChangeRecord:
    """页面变化记录"""
    action_name: str
    action_args: Dict[str, Any]
    thinking: str
    before_hash: Optional[str]
    after_hash: Optional[str]
    page_changed: bool
    timestamp: float = field(default_factory=time.time)
    step_index: int = 0


class PageChangeTracker:
    """页面变化追踪器
    
    记录Agent执行时的操作及页面hash变化，用于后续脚本生成时插入截图埋点。
    """
    
    def __init__(self, device_id: Optional[str] = None):
        self._page_changes: List[PageChangeRecord] = []
        self._current_hash: Optional[str] = None
        self._device_id = device_id
        self._step_count = 0
        self._last_screenshot_time = 0.0
        self._screenshot_min_interval = 0.5  # 最小截图间隔（秒）
    
    def record_action(
        self,
        action_name: str,
        action_args: Dict[str, Any],
        thinking: str,
        before_hash: Optional[str] = None,
        after_hash: Optional[str] = None,
        force_record: bool = False
    ) -> Optional[PageChangeRecord]:
        """记录操作及页面hash变化
        
        Args:
            action_name: 操作名称（如 click_text, launch_app）
            action_args: 操作参数
            thinking: Agent的思考过程
            before_hash: 操作前的页面hash
            after_hash: 操作后的页面hash
            force_record: 强制记录（忽略时间间隔限制）
        
        Returns:
            如果页面变化或强制记录，返回记录对象；否则返回None
        """
        # 检查时间间隔，避免短时间内重复截图
        current_time = time.time()
        if not force_record and (current_time - self._last_screenshot_time) < self._screenshot_min_interval:
            return None
        
        # 判断页面是否变化
        page_changed = (before_hash is not None and 
                       after_hash is not None and 
                       before_hash != after_hash)
        
        # 如果页面变化或强制记录，则记录
        if page_changed or force_record:
            self._step_count += 1
            record = PageChangeRecord(
                action_name=action_name,
                action_args=action_args,
                thinking=thinking,
                before_hash=before_hash,
                after_hash=after_hash,
                page_changed=page_changed,
                step_index=self._step_count
            )
            self._page_changes.append(record)
            self._current_hash = after_hash
            self._last_screenshot_time = current_time
            return record
        
        return None
    
    def get_page_changes(self) -> List[PageChangeRecord]:
        """获取所有页面变化记录"""
        return list(self._page_changes)
    
    def get_page_changes_dict(self) -> List[Dict[str, Any]]:
        """获取所有页面变化记录（字典格式）"""
        return [
            {
                "action_name": record.action_name,
                "action_args": record.action_args,
                "thinking": record.thinking,
                "before_hash": record.before_hash,
                "after_hash": record.after_hash,
                "page_changed": record.page_changed,
                "timestamp": record.timestamp,
                "step_index": record.step_index,
            }
            for record in self._page_changes
        ]
    
    def clear(self) -> None:
        """清空所有记录"""
        self._page_changes.clear()
        self._current_hash = None
        self._step_count = 0
        self._last_screenshot_time = 0.0
    
    def has_changes(self) -> bool:
        """检查是否有页面变化记录"""
        return len(self._page_changes) > 0
    
    def get_current_hash(self) -> Optional[str]:
        """获取当前页面hash"""
        return self._current_hash
    
    def update_current_hash(self, hash_value: str) -> None:
        """更新当前页面hash"""
        self._current_hash = hash_value


def calculate_phash(screenshot_bytes: bytes) -> Optional[str]:
    """计算感知哈希（pHash）
    
    Args:
        screenshot_bytes: 截图数据（bytes）
    
    Returns:
        pHash字符串，如果计算失败返回None
    """
    try:
        import io

        import imagehash
        from PIL import Image
        
        img = Image.open(io.BytesIO(screenshot_bytes))
        phash = imagehash.phash(img, hash_size=6)
        return str(phash)
    except ImportError as e:
        print(f"[页面追踪] 缺少依赖库: {e}")
        return None
    except Exception as e:
        print(f"[页面追踪] calculate_phash 失败: {type(e).__name__}: {e}")
        return None


def take_screenshot(device_id: Optional[str] = None) -> Optional[bytes]:
    """截取设备屏幕
    
    Args:
        device_id: 设备ID
    
    Returns:
        截图数据（bytes），如果失败返回None
    """
    try:
        adb_prefix = ["adb"]
        if device_id:
            adb_prefix += ["-s", device_id]
        
        result = subprocess.run(
            adb_prefix + ["exec-out", "screencap", "-p"],
            capture_output=True,
            timeout=15,
        )
        
        if result.returncode != 0:
            print(f"[页面追踪] adb screencap 失败: returncode={result.returncode}, stderr={result.stderr.decode('utf-8', errors='ignore')[:200]}")
            return None
        
        if len(result.stdout) < 100:
            print(f"[页面追踪] 截图数据过短: {len(result.stdout)} bytes，可能是空图或截图失败")
            return None
        
        return result.stdout
    except FileNotFoundError:
        print(f"[页面追踪] adb 命令未找到，请确保 adb 已添加到系统 PATH")
        return None
    except subprocess.TimeoutExpired:
        print(f"[页面追踪] adb screencap 超时")
        return None
    except Exception as e:
        print(f"[页面追踪] take_screenshot 失败: {type(e).__name__}: {e}")
        return None