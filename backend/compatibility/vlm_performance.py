"""VLM 请求缓存模块"""

import hashlib
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple


class VLMRequestCache:
    """VLM 请求缓存 - 基于截图内容的哈希值进行缓存"""

    def __init__(self, max_size: int = 100, ttl_seconds: int = 3600):
        self.cache: Dict[str, Tuple[float, Any]] = OrderedDict()
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds

    def _get_cache_key(self, image_base64: str, prompt: str) -> str:
        content = f"{image_base64}:{prompt}"
        return hashlib.md5(content.encode()).hexdigest()

    def get(self, image_base64: str, prompt: str) -> Optional[Any]:
        key = self._get_cache_key(image_base64, prompt)
        self._cleanup()
        if key in self.cache:
            timestamp, result = self.cache[key]
            if time.time() - timestamp < self.ttl_seconds:
                return result
            else:
                del self.cache[key]
        return None

    def set(self, image_base64: str, prompt: str, result: Any):
        key = self._get_cache_key(image_base64, prompt)
        self._cleanup()
        if len(self.cache) >= self.max_size:
            self.cache.popitem(last=False)
        self.cache[key] = (time.time(), result)

    def _cleanup(self):
        now = time.time()
        keys_to_delete = [
            key for key, (timestamp, _) in self.cache.items()
            if now - timestamp >= self.ttl_seconds
        ]
        for key in keys_to_delete:
            del self.cache[key]

    def clear(self):
        self.cache.clear()

    def stats(self) -> Dict[str, int]:
        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "ttl_seconds": self.ttl_seconds,
        }


_global_vlm_cache = VLMRequestCache(max_size=100, ttl_seconds=3600)


def get_global_vlm_cache() -> VLMRequestCache:
    return _global_vlm_cache


def set_global_vlm_cache_config(max_size: int = 100, ttl_seconds: int = 3600):
    global _global_vlm_cache
    _global_vlm_cache = VLMRequestCache(max_size=max_size, ttl_seconds=ttl_seconds)
