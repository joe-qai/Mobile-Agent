"""共享辅助函数"""
import os

# 报告列表缓存
_reports_cache = None
_reports_cache_time = 0
_reports_cache_ttl = 30  # 缓存有效期30秒


def invalidate_reports_cache():
    """使报告缓存失效"""
    global _reports_cache, _reports_cache_time
    _reports_cache = None
    _reports_cache_time = 0


def get_reports_cache():
    """获取报告缓存"""
    return _reports_cache, _reports_cache_time, _reports_cache_ttl


def set_reports_cache(cache, cache_time):
    """设置报告缓存"""
    global _reports_cache, _reports_cache_time
    _reports_cache = cache
    _reports_cache_time = cache_time


def get_apks_base_dir():
    """获取APK存储目录"""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base_dir, "uploads", "apks")
