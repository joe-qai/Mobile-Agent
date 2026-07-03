"""
APK 解析工具模块
提供 APK 包名提取等功能
"""
import subprocess
from typing import Optional


def extract_apk_package(file_path: str) -> Optional[str]:
    """
    使用 aapt2 解析 APK 包名（回退: 从二进制 AndroidManifest.xml 解析）
    
    Args:
        file_path: APK 文件路径
        
    Returns:
        包名字符串，解析失败返回 None
    """
    try:
        # 尝试使用 aapt2 获取包名
        result = subprocess.run(
            ["aapt2", "dump", "packagename", file_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            pkg = result.stdout.strip()
            if pkg:
                return pkg
    except FileNotFoundError:
        # aapt2 未安装
        pass
    except subprocess.TimeoutExpired:
        # 超时
        pass
    
    # 回退方案：从 manifest 二进制解析（简化实现）
    try:
        with open(file_path, "rb") as f:
            content = f.read()
            # 简单的包名提取（需要更完整的实现）
            # 这里只是一个占位符，实际需要更复杂的解析
            if b"AndroidManifest.xml" in content:
                # 基本的回退处理
                return None
    except Exception:
        pass
    
    return None
