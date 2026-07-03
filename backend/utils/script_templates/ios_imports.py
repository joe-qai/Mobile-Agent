import json
import os
import time
import urllib.request
import xml.etree.ElementTree as ET

WDA_URL = os.environ.get("WDA_URL", "http://localhost:8100").rstrip("/")


def _wda_request(method, endpoint, data=None):
    payload = None if data is None else json.dumps(data).encode("utf-8")
    request = urllib.request.Request(
        WDA_URL + endpoint,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_bounds(bounds):
    parts = str(bounds).strip("[]").replace("][", ",").split(",")
    if len(parts) != 4:
        raise ValueError(f"Invalid bounds: {bounds}")
    return tuple(int(float(part)) for part in parts)


def tap_bounds(bounds):
    left, top, right, bottom = _parse_bounds(bounds)
    x = (left + right) // 2
    y = (top + bottom) // 2
    _wda_request("POST", "/wda/tap/withCoordinates", {"x": x, "y": y})


def _node_bounds(node):
    attrs = node.attrib
    if all(key in attrs for key in ("x", "y", "width", "height")):
        left = int(float(attrs["x"]))
        top = int(float(attrs["y"]))
        right = left + int(float(attrs["width"]))
        bottom = top + int(float(attrs["height"]))
        return f"[{left},{top}][{right},{bottom}]"
    return attrs.get("bounds", "")


def _node_matches(node, by, value):
    text = attrs_text = node.attrib.get("name", "") or node.attrib.get("label", "") or node.attrib.get("value", "")
    if by == "text":
        return text == value
    if by in ("textContains", "textContain"):
        return value in text
    if by in ("content-desc", "contentDescription"):
        return node.attrib.get("label", "") == value
    if by in ("resource-id", "resourceId", "resource_id"):
        return node.attrib.get("name", "") == value
    return False


def _find_bounds(by, value):
    source = _wda_request("GET", "/source").get("value", "")
    root = ET.fromstring(source)
    for node in root.iter():
        if _node_matches(node, by, value):
            bounds = _node_bounds(node)
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
    _wda_request("POST", "/wda/launchApp", {"bundleId": package_name or app_name})
    time.sleep(1)


def input_text(text, clear_first=True):
    _wda_request("POST", "/wda/keys", {"value": list(str(text))})


def swipe(direction="up", distance="50%"):
    _wda_request("POST", "/wda/swipe", {"direction": direction})


def long_press_bounds(bounds, duration=1000):
    left, top, right, bottom = _parse_bounds(bounds)
    x = (left + right) // 2
    y = (top + bottom) // 2
    _wda_request("POST", "/wda/touchAndHold", {"x": x, "y": y, "duration": duration / 1000})


def long_press_selector(by, value, duration=1000):
    bounds = _find_bounds(by, value)
    if not bounds:
        raise RuntimeError(f"Element not found: {by}={value}")
    long_press_bounds(bounds, duration)


def back():
    _wda_request("POST", "/wda/back")


def home():
    _wda_request("POST", "/wda/home")


def wait(duration=2.0):
    time.sleep(float(duration))


def close_app(app_name, package_name=None):
    _wda_request("POST", "/wda/terminateApp", {"bundleId": package_name or app_name})
