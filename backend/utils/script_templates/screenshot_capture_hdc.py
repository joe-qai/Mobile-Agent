import base64
import json
import os
import subprocess
import time


def page_capture(step_name=None):
    try:
        device_id = os.environ.get("DEVICE_ID", "")
        cmd = ["hdc"]
        if device_id:
            cmd += ["-t", device_id]
        cmd += ["shell", "screencap", "-p", "/sdcard/screenshot.png"]
        subprocess.run(cmd, capture_output=True, timeout=30)
        
        cmd = ["hdc"]
        if device_id:
            cmd += ["-t", device_id]
        cmd += ["file", "recv", "/sdcard/screenshot.png", "/tmp/screenshot.png"]
        subprocess.run(cmd, capture_output=True, timeout=30)
        
        with open("/tmp/screenshot.png", "rb") as f:
            screenshot_base64 = base64.b64encode(f.read()).decode("utf-8")
        
        subprocess.run(["rm", "-f", "/tmp/screenshot.png"], capture_output=True)
        
        event = json.dumps({
            "type": "capture",
            "timestamp": time.time(),
            "step_name": step_name,
            "device_id": device_id,
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
