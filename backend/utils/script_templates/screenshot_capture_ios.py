import base64
import json
import os
import time
import urllib.request


def page_capture(step_name=None):
    try:
        wda_url = os.environ.get("WDA_URL", "http://localhost:8100").rstrip("/")
        response = urllib.request.urlopen(f"{wda_url}/wda/screenshot", timeout=30)
        screenshot_base64 = response.read().decode("utf-8")
        
        event = json.dumps({
            "type": "capture",
            "timestamp": time.time(),
            "step_name": step_name,
            "device_id": "",
            "image_size": len(screenshot_base64),
        })
        print(f"[CAPTURE_EVENT] {event}")
    except Exception:
        pass


def capture_when_stable(step_name=None, selector=None, device=None):
    try:
        max_wait = float(os.environ.get("SCREENSHOT_MAX_WAIT", "4.0"))
        poll_interval = float(os.environ.get("SCREENSHOT_POLL_INTERVAL", "0.25"))
    except ValueError:
        max_wait = 4.0
        poll_interval = 0.25

    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        time.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
        break
    page_capture(step_name)
