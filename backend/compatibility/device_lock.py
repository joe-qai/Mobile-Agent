"""设备锁机制 - 维护设备忙闲状态，避免并发冲突"""
import threading
import time
from typing import Dict, Optional, Set


class DeviceLockRegistry:
    """设备锁注册表"""
    
    def __init__(self):
        self._locks: Dict[str, threading.Lock] = {}
        self._lock_owners: Dict[str, str] = {}  # device_id -> task_id
        self._lock_timestamps: Dict[str, float] = {}  # device_id -> timestamp
        self._global_lock = threading.Lock()
    
    def _get_device_lock(self, device_id: str) -> threading.Lock:
        """获取或创建设备锁"""
        with self._global_lock:
            if device_id not in self._locks:
                self._locks[device_id] = threading.Lock()
            return self._locks[device_id]
    
    def acquire(self, device_id: str, task_id: str, timeout: int = 60) -> bool:
        """
        尝试获取设备锁
        
        Args:
            device_id: 设备ID
            task_id: 任务ID（锁的所有者）
            timeout: 超时时间（秒）
        
        Returns:
            True if lock acquired, False otherwise
        """
        device_lock = self._get_device_lock(device_id)
        end_time = time.time() + timeout
        
        while time.time() < end_time:
            if device_lock.acquire(blocking=False):
                with self._global_lock:
                    self._lock_owners[device_id] = task_id
                    self._lock_timestamps[device_id] = time.time()
                return True
            time.sleep(0.1)
        
        return False
    
    def release(self, device_id: str, task_id: str) -> bool:
        """
        释放设备锁
        
        Args:
            device_id: 设备ID
            task_id: 任务ID（必须与锁的所有者匹配）
        
        Returns:
            True if lock released, False if not owned by this task
        """
        with self._global_lock:
            if self._lock_owners.get(device_id) != task_id:
                return False
            
            device_lock = self._locks.get(device_id)
            if device_lock:
                try:
                    device_lock.release()
                except RuntimeError:
                    pass  # Lock wasn't acquired
            
            del self._lock_owners[device_id]
            del self._lock_timestamps[device_id]
            return True
    
    def release_all(self, task_id: str) -> int:
        """
        释放任务持有的所有锁
        
        Args:
            task_id: 任务ID
        
        Returns:
            释放的锁数量
        """
        released = 0
        with self._global_lock:
            devices_to_release = [
                device_id for device_id, owner in self._lock_owners.items()
                if owner == task_id
            ]
            
            for device_id in devices_to_release:
                device_lock = self._locks.get(device_id)
                if device_lock:
                    try:
                        device_lock.release()
                    except RuntimeError:
                        pass
                del self._lock_owners[device_id]
                del self._lock_timestamps[device_id]
                released += 1
        
        return released
    
    def is_locked(self, device_id: str) -> bool:
        """检查设备是否被锁定"""
        with self._global_lock:
            return device_id in self._lock_owners
    
    def get_lock_owner(self, device_id: str) -> Optional[str]:
        """获取设备锁的所有者"""
        with self._global_lock:
            return self._lock_owners.get(device_id)
    
    def get_locked_devices(self) -> Set[str]:
        """获取所有被锁定的设备"""
        with self._global_lock:
            return set(self._lock_owners.keys())
    
    def batch_acquire(self, device_ids: list, task_id: str, timeout: int = 60) -> bool:
        """
        批量获取多个设备锁
        
        Args:
            device_ids: 设备ID列表
            task_id: 任务ID
            timeout: 超时时间
        
        Returns:
            True if all locks acquired, False otherwise
        """
        # 先检查所有设备是否可用
        with self._global_lock:
            for device_id in device_ids:
                if device_id in self._lock_owners:
                    return False
        
        # 逐个获取锁
        acquired_devices = []
        try:
            for device_id in device_ids:
                if not self.acquire(device_id, task_id, timeout):
                    return False
                acquired_devices.append(device_id)
            return True
        except Exception:
            # 失败时释放已获取的锁
            for device_id in acquired_devices:
                self.release(device_id, task_id)
            return False


# 全局设备锁实例
device_lock_registry = DeviceLockRegistry()
