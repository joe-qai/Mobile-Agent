"""Docker 容器内自动发现 ADB 设备

策略(按优先级):
  1. USB 设备 — 通过 USB 直连自动识别
  2. 局域网扫描 — 自动推断容器所在网段,扫描 5555 端口
  3. 指定网段 — 通过 ADB_SCAN_SUBNETS 环境变量传入额外 CIDR
"""

import ipaddress
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


def log(msg: str):
    print(f"[discovery] {msg}", flush=True)


def adb_devices() -> set[str]:
    """返回当前已连接的设备列表"""
    try:
        r = subprocess.run(
            ["adb", "devices"],
            capture_output=True, text=True, timeout=5,
        )
        devices = set()
        for line in r.stdout.strip().split("\n")[1:]:
            line = line.strip()
            if line and "offline" not in line:
                dev = line.split("\t")[0]
                if dev:
                    devices.add(dev)
        return devices
    except Exception as e:
        log(f"adb devices 失败: {e}")
        return set()


def adb_connect(host: str, port: int = 5555) -> bool:
    """尝试连接一个 ADB 设备"""
    try:
        r = subprocess.run(
            ["adb", "connect", f"{host}:{port}"],
            capture_output=True, text=True, timeout=10,
        )
        if "connected" in r.stdout.lower():
            log(f"连接成功: {host}:{port}")
            return True
        return False
    except Exception:
        return False


def check_port(host: str, port: int = 5555, timeout: float = 1.0) -> bool:
    """检查主机端口是否开放"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        result = s.connect_ex((host, port))
        s.close()
        return result == 0
    except Exception:
        return False


def get_local_subnets() -> list[str]:
    """自动推断容器所在网段（纯 Python，不依赖 hostname 命令）"""
    subnets = []
    try:
        # 获取所有非回环的网络接口 IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        ip = ipaddress.IPv4Address(local_ip)
        if ip.is_private:
            network = ipaddress.IPv4Network(f"{ip}/24", strict=False)
            subnets.append(str(network))
            return subnets
    except Exception:
        pass

    # fallback: 枚举所有接口
    try:
        import subprocess
        r = subprocess.run(
            ["ip", "addr"], capture_output=True, text=True, timeout=5
        )
        import re
        for m in re.finditer(r'inet (\d+\.\d+\.\d+\.\d+)', r.stdout):
            try:
                ip = ipaddress.IPv4Address(m.group(1))
                if ip.is_private:
                    n = ipaddress.IPv4Network(f"{ip}/24", strict=False)
                    subnets.append(str(n))
            except ValueError:
                continue
    except Exception:
        pass

    return list(set(subnets))


def get_extra_subnets() -> list[str]:
    """从环境变量获取额外网段"""
    raw = os.environ.get("ADB_SCAN_SUBNETS", "")
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def scan_subnet(subnet: str) -> list[str]:
    """扫描一个子网内 5555 端口开放的主机"""
    try:
        network = ipaddress.IPv4Network(subnet, strict=False)
    except ValueError as e:
        log(f"无效网段 {subnet}: {e}")
        return []

    hosts = list(network.hosts())
    log(f"扫描 {subnet} ({len(hosts)} 个地址)...")

    found = []
    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = {pool.submit(check_port, str(h)): str(h) for h in hosts}
        for future in as_completed(futures):
            host = futures[future]
            if future.result():
                found.append(host)

    return found


def main():
    log("开始扫描 ADB 设备...")

    # 已连接设备（USB 直连）
    before = adb_devices()
    log(f"已连接设备: {before}")

    # 收集需要扫描的网段
    subnets = get_local_subnets()
    subnets += get_extra_subnets()

    if not subnets:
        log("未检测到网段，跳过局域网扫描")
    else:
        # 扫描网段
        all_found: set[str] = set()
        for subnet in subnets:
            try:
                hosts = scan_subnet(subnet)
                for h in hosts:
                    all_found.add(h)
            except Exception as e:
                log(f"扫描 {subnet} 失败: {e}")

        log(f"发现 {len(all_found)} 个潜在 ADB 设备")

        # 尝试连接
        new_count = 0
        for host in sorted(all_found):
            if adb_connect(host):
                new_count += 1
            time.sleep(0.1)  # 避免连接风暴

        log(f"新连接设备: {new_count}")

    # 最终设备列表
    after = adb_devices()
    log(f"当前在线设备: {after}")


if __name__ == "__main__":
    main()
