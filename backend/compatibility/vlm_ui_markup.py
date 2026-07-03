"""在截图上标注 VLM 发现的 UI 兼容性问题"""

import base64
import logging
import os
from io import BytesIO
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

SEVERITY_COLORS = {
    "blocker":    "#FF0000",
    "major":      "#FF6600",
    "minor":      "#FFAA00",
    "suggestion": "#3366FF",
}

SEVERITY_LABELS = {
    "blocker":    "BLOCKER",
    "major":      "MAJOR",
    "minor":      "MINOR",
    "suggestion": "SUGGEST",
}

_BOX_WIDTH = 3
_PADDING = 4


def _load_font(size: int = 14) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype("segoeui.ttf", size)
    except (IOError, OSError):
        try:
            return ImageFont.truetype("arial.ttf", size)
        except (IOError, OSError):
            try:
                return ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size
                )
            except (IOError, OSError):
                return ImageFont.load_default()


def annotate_image(
    image: Image.Image,
    issues: List[Dict[str, Any]],
) -> Image.Image:
    draw = ImageDraw.Draw(image)
    w, h = image.size
    font = _load_font(13)
    
    has_bbox_count = 0
    no_bbox_count = 0

    for idx, issue in enumerate(issues):
        bbox = issue.get("bbox")
        if not bbox or len(bbox) != 4:
            no_bbox_count += 1
            category = issue.get("category", "unknown")
            severity = issue.get("severity", "unknown")
            logger.debug(f"[截图标注] issue {idx+1} 缺少有效 bbox: category={category}, severity={severity}, bbox={bbox}")
            continue
        has_bbox_count += 1

        cx, cy, bw, bh = bbox
        severity = issue.get("severity", "minor")
        color = SEVERITY_COLORS.get(severity, "#FFAA00")

        x1 = int((cx - bw / 2) * w)
        y1 = int((cy - bh / 2) * h)
        x2 = int((cx + bw / 2) * w)
        y2 = int((cy + bh / 2) * h)

        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(w - 1, x2)
        y2 = min(h - 1, y2)

        draw.rectangle([x1, y1, x2, y2], outline=color, width=_BOX_WIDTH)

        label = f"{idx + 1} [{SEVERITY_LABELS.get(severity, '?')}]"
        bbox_text = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox_text[2] - bbox_text[0], bbox_text[3] - bbox_text[1]
        label_x1 = x1
        label_y1 = y1 - th - _PADDING * 2
        if label_y1 < 0:
            label_y1 = y2
        draw.rectangle(
            [label_x1, label_y1, label_x1 + tw + _PADDING * 2, label_y1 + th + _PADDING * 2],
            fill=color,
        )
        draw.text(
            (label_x1 + _PADDING, label_y1 + _PADDING),
            label,
            fill="white",
            font=font,
        )

    if has_bbox_count > 0 or no_bbox_count > 0:
        logger.debug(f"[截图标注] 标注完成: 总issues={len(issues)}, 已标注={has_bbox_count}, 缺少bbox={no_bbox_count}")
    
    return image


def annotate_file(
    image_path: str,
    issues: List[Dict[str, Any]],
    output_path: Optional[str] = None,
) -> str:
    img = Image.open(image_path).convert("RGB")
    annotated = annotate_image(img, issues)
    out = output_path or image_path.replace(".png", "_annotated.png").replace(
        ".jpg", "_annotated.jpg"
    )
    annotated.save(out, quality=92)
    return out


def annotate_base64(
    image_base64: str,
    issues: List[Dict[str, Any]],
) -> str:
    img_bytes = base64.b64decode(image_base64)
    img = Image.open(BytesIO(img_bytes)).convert("RGB")
    annotated = annotate_image(img, issues)
    buf = BytesIO()
    annotated.save(buf, format="JPEG", quality=92, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("utf-8")
