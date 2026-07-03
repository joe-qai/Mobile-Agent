import os
import subprocess
import time
import xml.etree.ElementTree as ET

DEVICE_ID = os.environ.get("DEVICE_ID", "")


def _hdc_shell(command):
    cmd = ["hdc"]
    if DEVICE_ID:
        cmd += ["-t", DEVICE_ID]
    cmd += ["shell", command]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _parse_bounds(bounds):
    parts = str(bounds).strip("[]").replace("][", ",").split(",")
    if len(parts) != 4:
        raise ValueError(f"Invalid bounds: {bounds}")
    return tuple(int(part) for part in parts)


def tap_bounds(bounds):
    left, top, right, bottom = _parse_bounds(bounds)
    x = (left + right) // 2
    y = (top + bottom) // 2
    _hdc_shell(f"input tap {x} {y}")


def _get_ui_xml():
    xml_content = _hdc_shell("uiautomator dump /sdcard/ui_dump.xml && cat /sdcard/ui_dump.xml")
    _hdc_shell("rm /sdcard/ui_dump.xml")
    return xml_content


def _node_matches(node, by, value):
    if by == "text":
        return node.attrib.get("text", "") == value
    if by in ("textContains", "textContain"):
        return value in node.attrib.get("text", "")
    if by in ("resource-id", "resourceId", "resource_id"):
        return node.attrib.get("resource-id", "") == value
    if by in ("content-desc", "contentDescription"):
        return node.attrib.get("content-desc", "") == value
    return False


def _find_bounds(by, value):
    root = ET.fromstring(_get_ui_xml())
    for node in root.iter():
        if _node_matches(node, by, value):
            bounds = node.attrib.get("bounds", "")
            if bounds:
                return bounds
    return None


def click_text(value):
    bounds = _find_bounds("text", value) or _find_bounds("textContains", value)
    if not bounds:
        raise RuntimeError(f"Text not found: {value}")
    tap_bounds(bounds)


def click_selector(by, value):
    if by == "text":
        click_text(value)
        return
    bounds = _find_bounds(by, value)
    if not bounds:
        raise RuntimeError(f"Element not found: {by}={value}")
    tap_bounds(bounds)


def launch_app(app_name, package_name=None):
    if package_name:
        _hdc_shell(f"am start -n {package_name}/.MainAbility")
    else:
        _hdc_shell(f"aa start -a {app_name}")
    time.sleep(1)


def input_text(text, clear_first=True):
    if clear_first:
        _hdc_shell("input keyevent KEYCODE_CTRL_A")
        time.sleep(0.1)
    _hdc_shell(f"input text {str(text).replace(' ', '%s')}")


def swipe(direction="up", distance="50%"):
    coords = {
        "up": (540, 1500, 540, 500),
        "down": (540, 500, 540, 1500),
        "left": (900, 960, 180, 960),
        "right": (180, 960, 900, 960),
    }[direction]
    _hdc_shell("input swipe %s %s %s %s" % coords)


def long_press_bounds(bounds, duration=1000):
    left, top, right, bottom = _parse_bounds(bounds)
    x = (left + right) // 2
    y = (top + bottom) // 2
    _hdc_shell(f"input swipe {x} {y} {x} {y} {int(duration)}")


def long_press_selector(by, value, duration=1000):
    bounds = _find_bounds(by, value)
    if not bounds:
        raise RuntimeError(f"Element not found: {by}={value}")
    long_press_bounds(bounds, duration)


def back():
    _hdc_shell("input keyevent KEYCODE_BACK")


def home():
    _hdc_shell("input keyevent KEYCODE_HOME")


def wait(duration=2.0):
    time.sleep(float(duration))


def close_app(app_name, package_name=None):
    _hdc_shell(f"am force-stop {package_name or app_name}")
