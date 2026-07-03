_prompt_cache: dict[str, str] = {}
_prompt_dir = __file__ and __file__.rsplit("\\", 1)[0] or "."


def load_prompt(name: str) -> str:
    if name not in _prompt_cache:
        path = f"{_prompt_dir}\\{name}"
        with open(path, encoding="utf-8") as f:
            _prompt_cache[name] = f.read()
    return _prompt_cache[name]
