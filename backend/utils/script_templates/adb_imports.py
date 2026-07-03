import os
import time

import pytest
import uiautomator2 as u2

from backend.utils.screenshot_collector import (
    ScreenshotCaptureCollector,
    capture_dom_signature,
    capture_screenshot,
    capture_when_stable,
    wait_until_scroll_idle,
    wait_until_stable,
)
