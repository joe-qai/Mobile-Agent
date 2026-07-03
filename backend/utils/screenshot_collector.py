"""Script-safe screenshot capture collector.

This module provides a lightweight mechanism to capture screenshots
during script execution without invoking VLM. Captures are stored
and can be batch-processed later.
"""

import base64
import io
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import List, Optional

# 在模块加载时（pytest 接管 stdout 之前）保存原始 stdout fd
# pytest 用 os.dup2 重定向 fd 1，连 sys.__stdout__ 也会被重定向
# 必须用 os.dup() 复制一份才能绕过
_real_stdout: Optional[io.TextIOWrapper] = None
_real_stdout_fd: Optional[int] = None
try:
    _real_stdout_fd = os.dup(1)
    _real_stdout = os.fdopen(_real_stdout_fd, "w", encoding="utf-8")
except Exception:
    pass


def _write_real(text: str):
    if _real_stdout:
        try:
            _real_stdout.write(text)
            _real_stdout.flush()
        except Exception:
            pass


@dataclass
class CaptureEvent:
    screenshot_base64: str
    timestamp: float = field(default_factory=lambda: time.time())
    step_name: Optional[str] = None
    device_id: Optional[str] = None


@dataclass(frozen=True)
class StableCaptureConfig:
    stable_frames: int = 2
    poll_interval: float = 0.25
    max_wait: float = 4.0


def _read_int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, ""))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def _read_float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, ""))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def _capture_context_active() -> bool:
    return bool(
        os.environ.get("COMPAT_CHILD_TASK_ID")
        or os.environ.get("VLM_EVENT_FILE")
    )


def _get_stable_capture_config() -> StableCaptureConfig:
    return StableCaptureConfig(
        stable_frames=_read_int_env("SCREENSHOT_STABLE_FRAMES", 2),
        poll_interval=_read_float_env("SCREENSHOT_POLL_INTERVAL", 0.25),
        max_wait=_read_float_env("SCREENSHOT_MAX_WAIT", 4.0),
    )


def _parse_selector_kwargs(selector: Optional[str]) -> Optional[dict]:
    if not selector:
        return None

    selector = selector.strip()
    for key in ("text", "resourceId", "description", "className"):
        prefix = f"{key}="
        if selector.startswith(prefix):
            raw = selector[len(prefix):].strip()
            if (raw.startswith('"') and raw.endswith('"')) or (
                raw.startswith("'") and raw.endswith("'")
            ):
                raw = raw[1:-1]
            return {key: raw}
    return None


def _wait_for_selector_anchor(device, selector: Optional[str], timeout: float) -> bool:
    kwargs = _parse_selector_kwargs(selector)
    if not device or not kwargs:
        return False
    try:
        return bool(device(**kwargs).wait(timeout=timeout))
    except Exception:
        return False


def _visual_fingerprint(screenshot_base64: Optional[str]) -> Optional[str]:
    if not screenshot_base64:
        return None
    try:
        import hashlib

        return hashlib.md5(screenshot_base64.encode("utf-8")).hexdigest()
    except Exception:
        return None


_LOADING_MARKERS = (
    "loading",
    "加载",
    "加载中",
    "请稍候",
    "正在加载",
    "刷新中",
    "progress",
    "ProgressBar",
    "ActivityIndicator",
)


def _device_ui_fingerprint(device=None) -> tuple[Optional[str], bool]:
    if device is None:
        return None, False

    if hasattr(device, "get_ui_tree"):
        try:
            ui_tree = device.get_ui_tree()
            raw_xml = getattr(ui_tree, "raw_xml", "") or ""
            if raw_xml:
                text = raw_xml
            else:
                elements = getattr(ui_tree, "elements", []) or []
                text = "|".join(str(getattr(elem, "to_dict", lambda: elem)()) for elem in elements)
            if text:
                is_loading = any(marker in text for marker in _LOADING_MARKERS)
                import hashlib

                return hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest(), is_loading
        except Exception:
            pass

    try:
        hierarchy = device.dump_hierarchy(compressed=True)
    except TypeError:
        try:
            hierarchy = device.dump_hierarchy()
        except Exception:
            return None, False
    except Exception:
        return None, False

    if not hierarchy:
        return None, False

    text = str(hierarchy)
    is_loading = any(marker in text for marker in _LOADING_MARKERS)
    try:
        import hashlib

        return hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest(), is_loading
    except Exception:
        return text, is_loading


def _wait_for_stable_fingerprint(
    fingerprint_getter,
    timeout: float,
    poll_interval: float,
    stable_frames: int,
) -> dict:
    deadline = time.monotonic() + max(0.0, timeout)
    last_hash = None
    unchanged = 0
    samples = 0
    loading_seen = False

    while time.monotonic() <= deadline:
        current_hash, is_loading = fingerprint_getter()
        samples += 1
        loading_seen = loading_seen or is_loading

        if current_hash and current_hash == last_hash and not is_loading:
            unchanged += 1
            if unchanged >= max(1, stable_frames - 1):
                return {
                    "stable": True,
                    "timed_out": False,
                    "samples": samples,
                    "loading_seen": loading_seen,
                }
        else:
            unchanged = 0
            last_hash = current_hash

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval, remaining))

    return {
        "stable": False,
        "timed_out": True,
        "samples": samples,
        "loading_seen": loading_seen,
    }


def wait_until_stable(
    device=None,
    timeout: float = 4.0,
    poll_interval: float = 0.25,
    stable_frames: int = 2,
) -> dict:
    """Wait until the current UI appears interactable and stable."""

    result = _wait_for_stable_fingerprint(
        lambda: _device_ui_fingerprint(device),
        timeout=timeout,
        poll_interval=poll_interval,
        stable_frames=stable_frames,
    )

    device_id = getattr(device, "device_id", None) or os.environ.get("DEVICE_ID")
    if result["samples"] <= 1 and not result["stable"] and device_id:
        collector = ScreenshotCaptureCollector(device_id=device_id)
        deadline = time.monotonic() + max(0.0, timeout)
        _wait_for_visual_stability(
            collector,
            deadline,
            poll_interval,
            stable_frames,
        )
    return result


def wait_until_scroll_idle(
    device=None,
    timeout: float = 3.0,
    poll_interval: float = 0.2,
    stable_frames: int = 2,
) -> dict:
    """Wait until a swipe's inertia has stopped and the UI is stable."""

    return wait_until_stable(
        device=device,
        timeout=timeout,
        poll_interval=poll_interval,
        stable_frames=stable_frames,
    )


def _wait_for_visual_stability(
    collector: "ScreenshotCaptureCollector",
    deadline: float,
    interval: float,
    stable_frames: int,
) -> None:
    last_hash = None
    unchanged = 0

    while time.monotonic() < deadline:
        screenshot = collector._take_screenshot()
        current_hash = _visual_fingerprint(screenshot)
        if current_hash and current_hash == last_hash:
            unchanged += 1
            if unchanged >= stable_frames - 1:
                return
        else:
            unchanged = 0
            last_hash = current_hash

        remaining = max(0, deadline - time.monotonic())
        if remaining <= 0:
            return
        time.sleep(min(interval, remaining))


class ScreenshotCaptureCollector:
    """Collects screenshot captures during script execution.
    
    This is designed to be injected into scripts to capture screenshots
    without invoking VLM. The captured screenshots are stored in memory
    and can be retrieved later for batch processing.
    """
    
    def __init__(self, device_id: Optional[str] = None):
        self._captures: List[CaptureEvent] = []
        self._device_id = device_id
    
    def _write(self, text: str):
        """写入到真实 stdout（绕过 pytest 的 stdout 捕获）"""
        _write_real(text)

    def capture_page(self, step_name: Optional[str] = None) -> None:
        """Capture current screen and store as a capture event.
        
        This method is designed to be called from scripts to capture
        the current screen without invoking VLM.
        
        Args:
            step_name: Optional name to identify this capture step
        """
        self._write(f"[CAPTURE_DEBUG] capture_page called: step_name={step_name}, device_id={self._device_id}\n")
        screenshot_base64 = self._take_screenshot()
        if screenshot_base64:
            event = CaptureEvent(
                screenshot_base64=screenshot_base64,
                step_name=step_name,
                device_id=self._device_id,
            )
            self._captures.append(event)
            
            # 输出完整的截图数据，供批量分析阶段使用
            event_json = json.dumps({
                "type": "capture",
                "timestamp": event.timestamp,
                "step_name": step_name,
                "device_id": self._device_id,
                "image_size": len(screenshot_base64),
                "screenshot_base64": screenshot_base64,
            })
            self._write(f"[CAPTURE_EVENT] {event_json}\n")
        else:
            self._write(f"[CAPTURE_DEBUG] _take_screenshot returned None for step: {step_name}\n")
    
    def get_captures(self) -> List[CaptureEvent]:
        """Get all collected capture events."""
        return list(self._captures)
    
    def clear(self) -> None:
        """Clear all collected captures."""
        self._captures.clear()
    
    def has_captures(self) -> bool:
        """Check if any captures have been collected."""
        return len(self._captures) > 0
    
    def _take_screenshot(self) -> Optional[str]:
        device_id = self._device_id or os.environ.get("DEVICE_ID")
        self._write(f"[CAPTURE_DEBUG] _take_screenshot: device_id={device_id}\n")
        if not device_id:
            self._write("[CAPTURE_DEBUG] No device_id, returning None\n")
            return None

        # 测试 ADB 是否可用
        try:
            test_result = subprocess.run(
                ["adb", "devices"],
                capture_output=True, text=True, timeout=5,
            )
            self._write(f"[CAPTURE_DEBUG] adb devices: returncode={test_result.returncode}, stdout={test_result.stdout.strip()[:200]}\n")
        except Exception as e:
            self._write(f"[CAPTURE_DEBUG] adb devices failed: {e}\n")

        adb_prefix = ["adb", "-s", device_id]

        methods = [
            ["exec-out", "screencap", "-p"],
            ["shell", "screencap", "-p"],
        ]

        for idx, method in enumerate(methods):
            try:
                self._write(f"[CAPTURE_DEBUG] Trying method {idx}: {method}\n")
                result = subprocess.run(
                    adb_prefix + method,
                    capture_output=True, timeout=15,
                )
                stdout_len = len(result.stdout)
                self._write(f"[CAPTURE_DEBUG] Method {idx}: returncode={result.returncode}, stdout_len={stdout_len}\n")
                if result.returncode == 0 and stdout_len > 100:
                    b64 = base64.b64encode(result.stdout).decode("utf-8")
                    self._write(f"[CAPTURE_DEBUG] Method {idx} SUCCESS, base64_len={len(b64)}\n")
                    return b64
            except subprocess.TimeoutExpired:
                self._write(f"[CAPTURE_DEBUG] Method {idx} timed out\n")
                continue
            except Exception as e:
                self._write(f"[CAPTURE_DEBUG] Method {idx} exception: {e}\n")
                continue

        self._write("[CAPTURE_DEBUG] All methods failed, returning None\n")
        return None


def parse_dom_signatures(output: str) -> List[dict]:
    """Parse DOM signature events from script output.

    Args:
        output: Script execution output containing DOM_SIG events

    Returns:
        List of parsed DOM signature dictionaries
    """
    events = []
    for line in output.split("\n"):
        line = line.strip()
        if line.startswith("[DOM_SIG]"):
            try:
                sig_json = line[len("[DOM_SIG]"):].strip()
                sig = json.loads(sig_json)
                events.append(sig)
            except json.JSONDecodeError:
                continue
    return events


def merge_dom_into_captures(
    capture_events: List[dict],
    dom_signatures: List[dict],
    max_time_delta: float = 2.0,
) -> List[dict]:
    """Merge DOM signatures into capture events by timestamp proximity.

    Each capture_event gets a 'dom_hash' field from the temporally
    closest DOM signature (within max_time_delta seconds).

    Args:
        capture_events: List of capture event dicts from parse_capture_events
        dom_signatures: List of DOM signature dicts from parse_dom_signatures
        max_time_delta: Maximum time difference (seconds) for matching

    Returns:
        Capture events with dom_hash added where a match was found
    """
    if not capture_events or not dom_signatures:
        return capture_events

    for cap in capture_events:
        cap_time = cap.get("timestamp", 0)
        best = None
        best_delta = float("inf")
        for sig in dom_signatures:
            sig_time = sig.get("timestamp", 0)
            delta = abs(cap_time - sig_time)
            if delta < best_delta and delta <= max_time_delta:
                best_delta = delta
                best = sig
        if best:
            cap["dom_hash"] = best["dom_hash"]
    return capture_events


def parse_capture_events(output: str) -> List[dict]:
    """Parse capture events from script output.
    
    Args:
        output: Script execution output containing capture events
        
    Returns:
        List of parsed capture event dictionaries
    """
    events = []
    for line in output.split("\n"):
        line = line.strip()
        if line.startswith("[CAPTURE_EVENT]"):
            try:
                event_json = line[len("[CAPTURE_EVENT]"):].strip()
                event = json.loads(event_json)
                events.append(event)
            except json.JSONDecodeError:
                continue
    return events


# 全局截图收集器实例，供脚本直接使用
_screenshot_collector_instance = None


def capture_screenshot(step_name: Optional[str] = None) -> None:
    """Capture current screen using the global collector.
    
    This is a simple wrapper function designed to be called from
    generated scripts without needing to manage the collector instance.
    
    Args:
        step_name: Optional name to identify this capture step
    """
    if not _capture_context_active():
        return
    global _screenshot_collector_instance
    if _screenshot_collector_instance is None:
        _screenshot_collector_instance = ScreenshotCaptureCollector(
            device_id=os.environ.get("DEVICE_ID")
        )
    _screenshot_collector_instance.capture_page(step_name)


def capture_dom_signature(step_name: Optional[str] = None) -> None:
    """Capture current screen's DOM structure as a hash signature.
    
    This function uses adb to dump the current UI hierarchy,
    computes an MD5 hash of the layout, and outputs it as
    a [DOM_SIG] event for cache keying in VLM compatibility analysis.
    
    Args:
        step_name: Optional name to identify this capture step
    """
    if not _capture_context_active():
        return
    device_id = os.environ.get("DEVICE_ID")
    if not device_id:
        return

    try:
        result = subprocess.run(
            ["adb", "-s", device_id, "shell", "uiautomator", "dump", "/dev/stdout"],
            capture_output=True, timeout=10,
        )
        if result.returncode != 0 or not result.stdout:
            return

        import hashlib
        import json
        import time
        dom_hash = hashlib.md5(result.stdout).hexdigest()
        sig = json.dumps({
            "type": "dom_signature",
            "step_name": step_name or "",
            "dom_hash": dom_hash,
            "timestamp": time.time(),
        })
        _write_real(f"[DOM_SIG] {sig}\n")
    except Exception:
        pass


def capture_when_stable(
    step_name: Optional[str] = None,
    selector: Optional[str] = None,
    device=None,
) -> None:
    """Wait for page stability, then capture screenshot and DOM signature."""
    if not _capture_context_active():
        return
    global _screenshot_collector_instance
    if _screenshot_collector_instance is None:
        _screenshot_collector_instance = ScreenshotCaptureCollector(
            device_id=os.environ.get("DEVICE_ID")
        )

    config = _get_stable_capture_config()
    deadline = time.monotonic() + config.max_wait
    remaining = max(0.0, deadline - time.monotonic())
    anchored = _wait_for_selector_anchor(device, selector, remaining) if selector else False

    if not anchored:
        _wait_for_visual_stability(
            _screenshot_collector_instance,
            deadline,
            config.poll_interval,
            config.stable_frames,
        )

    capture_screenshot(step_name)
    capture_dom_signature(step_name)
