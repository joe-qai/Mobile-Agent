from pathlib import Path
from typing import Dict, Optional

_TEMPLATES_DIR = Path(__file__).parent
_cache: Dict[str, str] = {}


def load_template(name: str) -> str:
    if name not in _cache:
        path = _TEMPLATES_DIR / name
        _cache[name] = path.read_text(encoding="utf-8")
    return _cache[name]
