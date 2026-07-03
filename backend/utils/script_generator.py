import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.llm import llm_protocols as llm_protocols_mod

from .script_templates import load_template

# ============ Prompt 加载函数 ============

PROMPTS_DIR = Path(__file__).parent.parent / "llm" / "prompts"


def load_prompt(prompt_name: str) -> str:
    """
    从 prompts 目录加载 prompt 文件
    
    Args:
        prompt_name: prompt 文件名（如 "vlm_ui_compatibility_v1.0.md"）
    
    Returns:
        prompt 内容字符串
    """
    prompt_path = PROMPTS_DIR / prompt_name
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    else:
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")


# 预加载常用 prompt
_SCRIPT_GENERATOR_SYSTEM_PROMPT = None


def get_script_generator_system_prompt() -> str:
    """获取脚本生成器系统 prompt"""
    global _SCRIPT_GENERATOR_SYSTEM_PROMPT
    if _SCRIPT_GENERATOR_SYSTEM_PROMPT is None:
        try:
            _SCRIPT_GENERATOR_SYSTEM_PROMPT = load_prompt("script_generator_default_v1.0.md")
        except FileNotFoundError:
            # 回退到内嵌 prompt
            _SCRIPT_GENERATOR_SYSTEM_PROMPT = """你是一个专业的移动端自动化测试工程师，精通各种移动端自动化测试框架。

你的职责是根据 AI Agent 执行的任务步骤，生成高质量、可运行的 pytest 测试脚本，特别注重UI兼容性测试。

生成规则：
1. 使用 pytest 框架，遵循标准测试结构（setup_method → test_xxx → teardown_method）
2. 根据设备类型选择正确的测试框架和语法
3. 每个操作后添加合理的等待时间（通常 1-3 秒）
4. 添加元素存在性检查和适当的异常处理
5. 包含清晰的注释说明每个步骤
6. 脚本必须可以直接运行
7. 设备连接：必须从环境变量 DEVICE_ID 获取设备序列号
8. UI兼容性测试：在关键操作点添加UI兼容性断言

输出格式：
- 只输出 Python 代码，不要有任何解释
- 代码必须完整，可以直接保存为 .py 文件运行"""
    return _SCRIPT_GENERATOR_SYSTEM_PROMPT


# ============ 辅助函数 ============

def clean_emoji(text: str) -> str:
    """Remove emoji characters from text to avoid encoding issues on Windows"""
    if not text:
        return text
    # Remove emojis (Unicode plane 1 and above)
    return re.sub(r'[\U00010000-\U0001ffff]+', '', text)

SUPPORTED_TOOLS: frozenset = frozenset(
    {
        "launch_app",
        "click_element",
        "input_text",
        "swipe",
        "swipe_up",
        "swipe_down",
        "swipe_left",
        "swipe_right",
        "long_press",
        "back",
        "home",
        "wait",
        "get_ui_tree",
        "get_current_app",
        "find_element",
        "close_app",
    }
)

# 测试报告模板
def _get_test_report_template() -> str:
    if not hasattr(_get_test_report_template, '_cache'):
        _get_test_report_template._cache = load_template('test_report.html')
    return _get_test_report_template._cache


def _format_param_value(val: Any) -> str:
    if isinstance(val, bool):
        return "True" if val else "False"
    if isinstance(val, str):
        return json.dumps(val, ensure_ascii=False)
    return str(val)


def _parse_args(args: Any) -> Dict[str, Any]:
    if isinstance(args, str):
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            return {}
    if args is None:
        return {}
    return args


def _build_tool_call(name: str, args: Dict[str, Any]) -> str:
    if name in {"back", "home", "get_ui_tree", "get_current_app"}:
        return f"{name}()"
    if not args:
        return f"{name}()"
    params = ", ".join(f"{k}={_format_param_value(v)}" for k, v in args.items())
    return f"{name}({params})"


def _strip_target_text(value: str) -> str:
    return value.strip(" 　，,。.!！?？、；;：:\"'“”‘’（）()[]【】")


def _normalize_app_name(value: str) -> str:
    cleaned = _strip_target_text(value)
    if not cleaned:
        return cleaned
    return re.sub(r"(?:app|APP|App|应用)$", "", cleaned).strip()


def _strip_input_text(value: str) -> str:
    return value.strip(" 　，,。；;：:\"'“”‘’（）()[]【】")


def infer_tool_calls_from_task(task_text: str) -> List[Dict[str, Any]]:
    """Infer a small set of deterministic tool calls from clear task wording."""
    task_text = (task_text or "").strip()
    if not task_text:
        return []

    tool_calls: List[Dict[str, Any]] = []

    open_match = re.search(
        r"(?:打开|启动|进入)\s*(.+?)(?:，|,|、|并|然后|后|并且|点击|点按|选择|$)",
        task_text,
    )
    if open_match:
        app_name = _normalize_app_name(open_match.group(1))
        if app_name:
            tool_calls.append(
                {
                    "function": {
                        "name": "launch_app",
                        "arguments": {"app_name": app_name},
                    }
                }
            )

    for click_match in re.finditer(
        r"(?:点击|点按|按下|选择)\s*(.+?)(?:$|按钮|入口|选项|，|,|、|然后|并|并且)",
        task_text,
    ):
        target = _strip_target_text(click_match.group(1))
        if not target:
            continue
        tool_calls.append(
            {
                "function": {
                    "name": "click_element",
                    "arguments": {"by": "text", "value": target},
                }
            }
        )

    for input_match in re.finditer(
        r"(?:输入|填写|录入)(?:密码|文本|内容|验证码|账号|帐号|手机号|用户名)?\s*[:：]?\s*(.+?)(?:$|，|,|。|；|;)",
        task_text,
    ):
        text = _strip_input_text(input_match.group(1))
        if not text:
            continue
        tool_calls.append(
            {
                "function": {
                    "name": "input_text",
                    "arguments": {"text": text},
                }
            }
        )

    return tool_calls


def _infer_body_from_task(task_text: str, device_type: str = "adb") -> str:
    inferred_step = {
        "step": 0,
        "thinking": "根据自然语言任务生成脚本",
        "tool_calls": infer_tool_calls_from_task(task_text),
        "success": True,
    }
    return framework_template_mapping([inferred_step], device_type=device_type)


def _merge_script_bodies(primary: str, fallback: str) -> str:
    if isinstance(primary, dict):
        primary_body = primary.get('body', '')
        launch_app = primary.get('launch_app')
        close_app = primary.get('close_app')
        
        if not primary_body:
            return fallback
        if not fallback:
            return primary

        lines = primary_body.splitlines()
        existing_statements = {
            line.strip()
            for line in lines
            if line.strip() and not line.strip().startswith("#")
        }
        for line in fallback.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                if line not in lines:
                    lines.append(line)
                continue
            if stripped not in existing_statements:
                lines.append(line)
                existing_statements.add(stripped)
        
        return {
            'body': "\n".join(lines),
            'launch_app': launch_app,
            'close_app': close_app
        }
    
    if not primary:
        return fallback
    if not fallback:
        return primary

    lines = primary.splitlines()
    existing_statements = {
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    }
    for line in fallback.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            if line not in lines:
                lines.append(line)
            continue
        if stripped not in existing_statements:
            lines.append(line)
            existing_statements.add(stripped)
    return "\n".join(lines)


def template_mapping(step_results: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for step in step_results:
        if not step.get("success"):
            continue
        thinking = (step.get("thinking") or "").strip()
        tool_calls = step.get("tool_calls") or []
        step_lines: List[str] = []
        for tc in tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "")
            if name == "finish":
                continue
            if name not in SUPPORTED_TOOLS:
                continue
            args = _parse_args(func.get("arguments"))
            statement = _build_tool_call(name, args)
            step_lines.append(statement)
        if step_lines:
            if thinking:
                comment = clean_emoji(thinking)[:80]
                lines.append(f"# {comment}")
            lines.extend(step_lines)
    return "\n".join(lines)


def _normalize_device_type(device_type: str) -> str:
    normalized = (device_type or "adb").strip().lower()
    aliases = {
        "android": "adb",
        "harmony": "hdc",
        "harmonyos": "hdc",
        "wda": "ios",
    }
    if normalized in {"adb", "hdc", "ios"}:
        return normalized
    return aliases.get(normalized, "adb")


def _lookup_package_name(app_name: str) -> Optional[str]:
    if not app_name:
        return None
    from backend.config.apps import get_package_name

    return get_package_name(app_name)


def _normalize_click_args(args: Dict[str, Any]) -> tuple[str, str]:
    by = (
        args.get("by")
        or ("resource-id" if "resource_id" in args else "")
        or ("text" if "text" in args else "")
        or ("content-desc" if "content_desc" in args else "")
        or ("bounds" if "bounds" in args else "")
    )
    value = (
        args.get("value")
        or args.get("resource_id")
        or args.get("text")
        or args.get("content_desc")
        or args.get("bounds")
        or ""
    )
    return str(by), str(value)


def _build_uiautomator2_swipe_call(direction: Any = "up") -> str:
    direction_key = str(direction or "up").lower()
    if direction_key not in {"up", "down", "left", "right"}:
        direction_key = "up"
    points = {
        "up": ("0.5", "0.8", "0.5", "0.25"),
        "down": ("0.5", "0.25", "0.5", "0.8"),
        "left": ("0.85", "0.5", "0.15", "0.5"),
        "right": ("0.15", "0.5", "0.85", "0.5"),
    }[direction_key]
    return "\n".join(
        [
            "width, height = self.d.window_size()",
            (
                "self.d.swipe("
                f"int(width * {points[0]}), int(height * {points[1]}), "
                f"int(width * {points[2]}), int(height * {points[3]}), "
                "duration=0.5)"
            ),
            "wait_until_scroll_idle(self.d)",
        ]
    )


def _uiautomator2_post_action_wait(name: str) -> str:
    if name in {
        "click_element",
        "launch_app",
        "close_app",
        "back",
        "home",
        "long_press",
    }:
        return "wait_until_stable(self.d)"
    return ""


def _build_uiautomator2_selector_click(selector_expr: str) -> str:
    selector_label = repr(selector_expr)
    return "\n".join(
        [
            f"target = self.d({selector_expr})",
            "if not target.wait(timeout=10):",
            (
                "    current = self.d.app_current() if hasattr(self.d, 'app_current') "
                "else {}"
            ),
            "    try:",
            "        hierarchy = self.d.dump_hierarchy(compressed=True)",
            "    except TypeError:",
            "        hierarchy = self.d.dump_hierarchy()",
            "    raise AssertionError(",
            f"        'element not found: ' + {selector_label} + ",
            (
                "        f\"; current={current}; hierarchy={hierarchy[:2000]}\""
            ),
            "    )",
            "target.click()",
        ]
    )


def _build_framework_call(name: str, args: Dict[str, Any], device_type: str) -> str:
    if name == "click_element":
        by, value = _normalize_click_args(args)
        if by == "resource-id":
            return f"click_selector(by=resourceId, value={_format_param_value(value)})"
        if by == "bounds":
            return f"tap_bounds({_format_param_value(value)})"
        if by == "text":
            return f"click_text({_format_param_value(value)})"
        return (
            f"click_selector(by={_format_param_value(by)}, "
            f"value={_format_param_value(value)})"
        )

    if name == "launch_app":
        app_name = str(args.get("app_name") or args.get("package_name") or "")
        package_name = args.get("package_name")
        if device_type == "adb" and not package_name:
            package_name = _lookup_package_name(app_name)
        params = [f"app_name={_format_param_value(app_name)}"]
        if package_name:
            params.append(f"package_name={_format_param_value(package_name)}")
        return f"launch_app({', '.join(params)})"

    if name == "input_text":
        params = [f"text={_format_param_value(args.get('text', ''))}"]
        if "clear_first" in args:
            params.append(f"clear_first={_format_param_value(args['clear_first'])}")
        return f"input_text({', '.join(params)})"

    if name == "swipe":
        params = []
        if "direction" in args:
            params.append(f"direction={_format_param_value(args['direction'])}")
        if "distance" in args:
            params.append(f"distance={_format_param_value(args['distance'])}")
        return f"swipe({', '.join(params)})" if params else "swipe()"

    if name == "long_press":
        by, value = _normalize_click_args(args)
        duration = args.get("duration", 1000)
        if by == "bounds":
            return (
                f"long_press_bounds(bounds={_format_param_value(value)}, "
                f"duration={_format_param_value(duration)})"
            )
        return (
            f"long_press_selector(by={_format_param_value(by)}, "
            f"value={_format_param_value(value)}, "
            f"duration={_format_param_value(duration)})"
        )

    if name in {"back", "home"}:
        return f"{name}()"

    if name == "wait":
        return f"wait(duration={_format_param_value(args.get('duration', 2.0))})"

    if name == "close_app":
        app_name = str(args.get("app_name") or args.get("package_name") or "")
        package_name = args.get("package_name")
        params = [f"app_name={_format_param_value(app_name)}"]
        if package_name:
            params.append(f"package_name={_format_param_value(package_name)}")
        return f"close_app({', '.join(params)})"

    return ""


def framework_template_mapping(
    step_results: List[Dict[str, Any]], device_type: str = "adb"
) -> str:
    device_type = _normalize_device_type(device_type)
    lines: List[str] = []
    launch_app_line = None
    launch_app_package = None
    close_app_line = None
    step_num = 1
    
    for step in step_results:
        if not step.get("success"):
            continue
        tool_calls = step.get("tool_calls") or []
        for tc in tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "")
            if name == "finish" or name not in SUPPORTED_TOOLS:
                continue
            if name in {"get_ui_tree", "get_current_app", "find_element"}:
                continue
            args = _parse_args(func.get("arguments"))
            
            if device_type == "adb":
                if name == "launch_app":
                    app_name = str(args.get("app_name") or args.get("package_name") or "")
                    package_name = args.get("package_name") or _lookup_package_name(app_name)
                    if package_name:
                        launch_app_line = f"self.d.app_start({_format_param_value(package_name)})"
                        launch_app_package = package_name
                    else:
                        launch_app_line = f"self.d.app_start({_format_param_value(app_name)})"
                        launch_app_package = None
                    continue
                elif name == "close_app":
                    app_name = str(args.get("app_name") or args.get("package_name") or "")
                    package_name = args.get("package_name")
                    target = package_name or app_name
                    close_app_line = f"self.d.app_stop({_format_param_value(target)})"
                    continue
                else:
                    statement = _build_uiautomator2_call(name, args)
            else:
                statement = _build_framework_call(name, args, device_type)
            
            if statement:
                post_wait = (
                    _uiautomator2_post_action_wait(name)
                    if device_type == "adb"
                    else ""
                )
                if post_wait:
                    statement = f"{statement}\n{post_wait}"
                lines.append(statement)
                step_num += 1
    
    # 返回body和setup/teardown的app操作
    return {
        'body': "\n".join(lines),
        'launch_app': launch_app_line,
        'close_app': close_app_line,
        'launch_app_package': launch_app_package,
    }


def llm_polish(draft: str, task_text: str, step_results: List[Dict[str, Any]]) -> str:
    llm = llm_protocols_mod.llm_protocol
    if llm is None:
        return draft

    system_prompt = (
        "你是一个Python脚本优化专家。请将以下任务步骤转换为优化后的Python脚本。"
    )
    user_prompt = (
        f"任务: {task_text}\n\n"
        f"步骤结果:\n{json.dumps(step_results, ensure_ascii=False, indent=2)}\n\n"
        f"草稿脚本:\n{draft}\n\n"
        "要求:\n"
        "1. 保持线性结构，不要使用函数或类\n"
        "2. 从thinking中提取注释（# 开头）\n"
        "3. 用try/except包装操作\n"
        "4. 合并冗余步骤\n"
        "5. 只输出Python代码，不要有解释\n"
        "6. 只需要输出main函数内部的逻辑代码，不要写import语句或def main()定义\n\n"
        "优化后的代码:\n"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    try:
        result = llm.chat_completion(messages)
        if isinstance(result, str) and result and not result.startswith("Error:"):
            return result
    except Exception:
        pass
    return draft


def _get_adb_imports() -> str:
    if not hasattr(_get_adb_imports, '_cache'):
        _get_adb_imports._cache = load_template('adb_imports.py')
    return _get_adb_imports._cache


def _get_hdc_imports() -> str:
    if not hasattr(_get_hdc_imports, '_cache'):
        _get_hdc_imports._cache = load_template('hdc_imports.py')
    return _get_hdc_imports._cache


def _get_ios_imports() -> str:
    if not hasattr(_get_ios_imports, '_cache'):
        _get_ios_imports._cache = load_template('ios_imports.py')
    return _get_ios_imports._cache





def _get_screenshot_capture_adb_imports() -> str:
    if not hasattr(_get_screenshot_capture_adb_imports, '_cache'):
        _get_screenshot_capture_adb_imports._cache = load_template('screenshot_capture.py')
    return _get_screenshot_capture_adb_imports._cache


def _get_screenshot_capture_hdc_imports() -> str:
    if not hasattr(_get_screenshot_capture_hdc_imports, '_cache'):
        _get_screenshot_capture_hdc_imports._cache = load_template('screenshot_capture_hdc.py')
    return _get_screenshot_capture_hdc_imports._cache


def _get_screenshot_capture_ios_imports() -> str:
    if not hasattr(_get_screenshot_capture_ios_imports, '_cache'):
        _get_screenshot_capture_ios_imports._cache = load_template('screenshot_capture_ios.py')
    return _get_screenshot_capture_ios_imports._cache


def _get_screenshot_capture_imports(device_type: str) -> str:
    """Get screenshot capture imports for the specified device type."""
    device_type = _normalize_device_type(device_type)
    if device_type == "hdc":
        return _get_screenshot_capture_hdc_imports()
    if device_type == "ios":
        return _get_screenshot_capture_ios_imports()
    return _get_screenshot_capture_adb_imports()


def add_screenshot_captures_to_script(
    script_content: str,
    device_type: str = "adb",
    page_changes: Optional[List[Dict[str, Any]]] = None
) -> str:
    """Inject lightweight screenshot capture calls into script.
    
    This replaces VLM UI assertions with simple screenshot captures
    that can be analyzed later in batch.
    
    Args:
        script_content: Original script content
        device_type: Device type (adb/hdc/ios)
        page_changes: Page change records from Agent execution (optional)
        
    Returns:
        Script with capture_screenshot() calls injected
    """
    # 如果脚本 body（import/class/def 之后）已经包含截图捕获调用，跳过二次注入
    after_imports = False
    for line in script_content.split("\n"):
        stripped = line.lstrip()
        if not after_imports and (stripped.startswith("class ") or stripped.startswith("def ")):
            after_imports = True
        if after_imports and re.search(r'\b(capture_when_stable|capture_screenshot)\s*\(', stripped):
            return script_content

    # 如果有页面变化记录，使用智能插入策略
    if page_changes:
        return _add_screenshot_by_page_changes(script_content, device_type, page_changes)
    
    # 否则回退到启发式规则（向后兼容）
    return _add_screenshot_by_heuristic(script_content, device_type)


def _add_screenshot_by_page_changes(
    script_content: str,
    device_type: str,
    page_changes: List[Dict[str, Any]]
) -> str:
    """基于页面变化记录插入截图埋点
    
    根据页面变化记录中的图片hash差异，在操作后插入截图埋点：
    - 对比相邻操作后的页面hash差异
    - 如果相邻两步hash无差异，只在前一步操作后插入截图
    - 如果相邻两步hash有差异，两步操作后都插入截图
    - 最后一个操作后总是插入截图
    - 在 setup_method/teardown_method 中不插入截图
    
    Args:
        script_content: 原始脚本内容
        device_type: 设备类型
        page_changes: 页面变化记录（包含step/thinking/page_changed）
    
    Returns:
        插入截图埋点后的脚本
    """
    auto_inject = os.environ.get("AUTO_INJECT_SCREENSHOT", "true").lower() == "true"
    if not auto_inject:
        return script_content
    
    screenshot_imports = _get_screenshot_capture_imports(device_type)
    lines = script_content.split("\n")
    # 第一步：收集所有操作行（在测试函数中的操作）
    action_lines = []
    in_test_function = False
    
    for i, line in enumerate(lines):
        if "def test_" in line and "(" in line:
            in_test_function = True
        elif in_test_function and line.strip().startswith("def ") and "test_" not in line:
            in_test_function = False
        
        if in_test_function and _is_action_line(line):
            action_lines.append({
                'line_num': i,
                'line': line,
                'action_name': _extract_action_name(line)
            })
    
    # 第二步：确定哪些操作行需要插入截图
    # 逻辑：对比相邻操作的page_changed，决定是否插入截图
    # 规则：只有当前操作后页面变化了，才在当前操作后插入截图（最后一个操作总是插入）
    insert_after_lines = set()
    
    # 过滤掉 launch_app 和 close_app 的步骤，因为它们在 setup/teardown 中
    filtered_page_changes = []
    for page_change in page_changes:
        thinking = page_change.get("thinking", "")
        if "打开" in thinking or "启动" in thinking or "launch" in thinking.lower():
            continue  # 跳过 launch_app
        if "关闭" in thinking or "退出" in thinking or "close" in thinking.lower() or "stop" in thinking.lower():
            continue  # 跳过 close_app
        filtered_page_changes.append(page_change)
    
    for idx, action in enumerate(action_lines):
        # 最后一个操作后总是插入截图
        if idx == len(action_lines) - 1:
            insert_after_lines.add(action['line_num'])
            continue
        
        # 获取当前操作的页面变化状态
        current_page_change = filtered_page_changes[idx] if idx < len(filtered_page_changes) else {'page_changed': True}
        current_changed = current_page_change.get('page_changed', False)
        
        # 如果当前操作后页面变化了，就在当前操作后插入截图
        if current_changed:
            insert_after_lines.add(action['line_num'])
    
    # 第三步：插入截图调用。采集当前动作结果时，延迟到下一动作之前执行。
    new_lines = []
    pending_capture = None
    
    for i, line in enumerate(lines):
        if pending_capture and (
            _is_action_line(line) or line.strip().startswith("def ")
        ):
            new_lines.append(pending_capture)
            pending_capture = None

        new_lines.append(line)
        
        # 在需要的操作行后记录截图，实际在下一动作前执行
        if i in insert_after_lines:
            # 计算当前行的缩进
            current_indent = _line_indent(line)
            
            # 查找对应的操作，获取描述
            step_name = None
            for action_idx, action in enumerate(action_lines):
                if action['line_num'] == i:
                    # 使用 action_idx 从 filtered_page_changes 中获取对应的 thinking
                    if action_idx < len(filtered_page_changes):
                        thinking = filtered_page_changes[action_idx].get("thinking", "")
                        if thinking:
                            # 提取关键动作描述
                            if "打开" in thinking or "启动" in thinking:
                                step_name = "launch_app"
                            elif "点击" in thinking:
                                step_name = "click"
                            elif "滑动" in thinking or "上滑" in thinking or "下滑" in thinking:
                                step_name = "swipe"
                            elif "输入" in thinking:
                                step_name = "input"
                            elif "返回" in thinking:
                                step_name = "back"
                            else:
                                step_name = "action"
                    break
            
            pending_capture = _capture_when_stable_call(
                current_indent,
                step_name or "action",
                _next_action_selector(lines, i),
                _action_device_expr(_next_action_line(lines, i)),
            )

    if pending_capture:
        new_lines.append(pending_capture)
    
    result = "\n".join(new_lines)
    if not script_content.strip():
        return result
    if "capture_when_stable" not in _imports_section(result):
        result = f"{screenshot_imports}\n\n{result}"
    return result


def _is_action_line(line: str) -> bool:
    """判断是否是需要插入截图的操作行
    
    Args:
        line: 代码行
    
    Returns:
        是否是操作行
    """
    line_stripped = line.strip()
    if not line_stripped or line_stripped.startswith("#"):
        return False
    
    action_patterns = [
        "launch_app(",
        "click_text(",
        "click_selector(",
        "tap_bounds(",
        "input_text(",
        "swipe(",
        ".app_start(",
        ".click(",
        ".send_keys(",
        ".swipe(",
    ]
    
    return any(pattern in line for pattern in action_patterns)


def _line_indent(line: str) -> str:
    current_indent = ""
    for char in line:
        if char in " \t":
            current_indent += char
        else:
            break
    return current_indent


def _imports_section(text: str) -> str:
    """Return the top-of-file import region (before the first class/def).

    Used to decide whether a symbol is *imported*, ignoring same-named calls
    that appear later in the script body.
    """
    section_lines = []
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("class ") or stripped.startswith("def "):
            break
        section_lines.append(line)
    return "\n".join(section_lines)


def _extract_action_selector(line: str) -> Optional[str]:
    """Extract a semantic selector from an action line when one is explicit."""
    patterns = [
        r'd\(\s*(text\s*=\s*["\'][^"\']+["\'])\s*\)',
        r'd\(\s*(resourceId\s*=\s*["\'][^"\']+["\'])\s*\)',
        r'd\(\s*(description\s*=\s*["\'][^"\']+["\'])\s*\)',
        r'click_text\(\s*(["\'][^"\']+["\'])',
        r'click_selector\(\s*(["\'][^"\']+["\'])',
    ]
    for pattern in patterns:
        match = re.search(pattern, line)
        if not match:
            continue
        value = match.group(1).strip()
        if pattern.startswith("click_text"):
            return f"text={value}"
        if pattern.startswith("click_selector"):
            return value.strip("\"'")
        return value.replace(" ", "")
    return None


def _action_device_expr(line: str) -> Optional[str]:
    if "self.d" in line or "target.click(" in line:
        return "self.d"
    return None


def _target_assignment_selector_before(lines: List[str], index: int) -> Optional[str]:
    for previous in reversed(lines[:index]):
        stripped = previous.strip()
        if stripped.startswith("def ") or stripped.startswith("class "):
            break
        if "target = self.d(" in previous:
            selector = _extract_action_selector(previous)
            if selector:
                return selector
            break
    return None


def _action_selector(lines: List[str], index: int) -> Optional[str]:
    selector = _extract_action_selector(lines[index])
    if selector:
        return selector
    if "target.click(" in lines[index]:
        return _target_assignment_selector_before(lines, index)
    return None


def _next_action_line(lines: List[str], start_idx: int) -> str:
    for line in lines[start_idx + 1:]:
        if _is_action_line(line):
            return line
    return ""


def _next_action_selector(lines: List[str], start_index: int) -> Optional[str]:
    for index in range(start_index + 1, len(lines)):
        if _is_action_line(lines[index]):
            return _action_selector(lines, index)
    return None


def _capture_when_stable_call(
    indent: str,
    step_name: str,
    selector: Optional[str],
    device_expr: Optional[str] = None,
) -> str:
    device_arg = f", device={device_expr}" if device_expr else ""
    if selector:
        safe_selector = selector.replace("\\", "\\\\").replace("'", "\\'")
        return (
            f'{indent}capture_when_stable("{step_name}", '
            f"selector='{safe_selector}'{device_arg})"
        )
    return f'{indent}capture_when_stable("{step_name}"{device_arg})'


def _add_screenshot_by_heuristic(
    script_content: str,
    device_type: str
) -> str:
    """Insert screenshot capture based on heuristic rules (backward compatible)"""
    auto_inject = os.environ.get("AUTO_INJECT_SCREENSHOT", "true").lower() == "true"
    if not auto_inject:
        return script_content
    
    lines = script_content.split("\n")
    new_lines = []
    
    screenshot_imports = _get_screenshot_capture_imports(device_type)
    in_main_function = False
    in_test_function = False
    in_setup_method = False
    in_teardown_method = False
    pending_capture = None
    has_app_start = any("app_start(" in line or "launch_app(" in line for line in lines)
    home_capture_inserted = False
    
    for i, line in enumerate(lines):
        if (
            has_app_start
            and not home_capture_inserted
            and not pending_capture
            and (in_main_function or in_test_function)
            and _is_action_line(line)
            and "app_start(" not in line
            and "launch_app(" not in line
        ):
            new_lines.append(
                _capture_when_stable_call(
                    _line_indent(line),
                    "home",
                    _action_selector(lines, i),
                    _action_device_expr(line),
                )
            )
            home_capture_inserted = True

        if pending_capture and (in_main_function or in_test_function) and (
            _is_action_line(line) or line.strip().startswith("def ")
        ):
            new_lines.append(pending_capture)
            pending_capture = None

        new_lines.append(line)
        
        if (in_main_function or in_test_function) and _is_action_line(line):
            next_line = lines[i+1].strip() if i + 1 < len(lines) else ""
            
            if next_line.startswith(("capture_screenshot", "capture_when_stable")):
                continue
            
            if "app_start(" in line:
                step_name = "home"
                home_capture_inserted = True
            else:
                action_name = _extract_action_name(line)
                step_name = f"after_{action_name}" if action_name else "action"
            pending_capture = _capture_when_stable_call(
                _line_indent(line),
                step_name,
                _next_action_selector(lines, i),
                _action_device_expr(_next_action_line(lines, i)),
            )
        
        if "def main():" in line:
            in_main_function = True
            in_test_function = False
            in_setup_method = False
            in_teardown_method = False
        elif in_main_function and line.strip().startswith("def ") and "main" not in line:
            in_main_function = False
        
        if "def setup_method" in line and "(" in line:
            in_setup_method = True
            in_test_function = False
            in_main_function = False
        elif in_setup_method and line.strip().startswith("def ") and "setup_method" not in line:
            in_setup_method = False
        
        if "def teardown_method" in line and "(" in line:
            in_teardown_method = True
            in_test_function = False
            in_main_function = False
        elif in_teardown_method and line.strip().startswith("def ") and "teardown_method" not in line:
            in_teardown_method = False
        
        if "def test_" in line and "(" in line:
            in_test_function = True
            in_main_function = False
            in_setup_method = False
            in_teardown_method = False
        elif in_test_function and line.strip().startswith("def ") and "test_" not in line:
            in_test_function = False

    if pending_capture:
        new_lines.append(pending_capture)
    
    result = "\n".join(new_lines)
    if not script_content.strip():
        return result
    if "capture_when_stable" not in _imports_section(result):
        result = f"{screenshot_imports}\n\n{result}"
    return result


def _build_action_line_map(lines: List[str]) -> Dict[str, int]:
    """构建操作名称到行号的映射
    
    Args:
        lines: 脚本行列表
    
    Returns:
        操作名称到行号的映射
    """
    action_line_map = {}
    for i, line in enumerate(lines):
        action_name = _extract_action_name(line)
        if action_name:
            action_line_map[action_name] = i
    return action_line_map


def _extract_action_name(line: str) -> Optional[str]:
    """从代码行中提取操作名称
    
    支持两种模式：
    - 普通函数调用：launch_app(...)
    - 对象方法调用：self.d.app_start(...)
    
    Args:
        line: 代码行
    
    Returns:
        操作名称，如果未找到返回None
    """
    import re
    # 匹配最后一个方法调用：self.d(text="Login").click() -> click
    method_matches = re.findall(r'\.\s*(\w+)\s*\(', line)
    if method_matches:
        return method_matches[-1]
    # 匹配普通函数调用：launch_app(...), click_text(...), etc.
    match = re.search(r'(\w+)\s*\(', line)
    if match:
        return match.group(1)
    return None


def _find_page_change(
    page_changes: List[Dict[str, Any]],
    action_name: str,
    line_num: int
) -> Optional[Dict[str, Any]]:
    """查找对应的页面变化记录
    
    Args:
        page_changes: 页面变化记录列表
        action_name: 操作名称
        line_num: 行号
    
    Returns:
        页面变化记录，如果未找到返回None
    """
    # 简单匹配：找到第一个匹配action_name的记录
    for page_change in page_changes:
        if page_change.get("action_name") == action_name:
            return page_change
    return None


def _get_footer() -> str:
    if not hasattr(_get_footer, '_cache'):
        _get_footer._cache = load_template('footer.py')
    return _get_footer._cache


def _get_framework_imports(device_type: str) -> str:
    device_type = _normalize_device_type(device_type)
    if device_type == "hdc":
        return _get_hdc_imports()
    if device_type == "ios":
        return _get_ios_imports()
    return _get_adb_imports()


def _build_adb_setup_lifecycle(launch_app: str = None, launch_app_package: str = None) -> str:
    lines = []
    if launch_app_package:
        lines.extend([
            f"self.d.app_stop({_format_param_value(launch_app_package)})",
            "time.sleep(1)",
        ])
    if launch_app:
        lines.extend([launch_app, "wait_until_stable(self.d)"])
    return "\n".join(lines)


def _build_adb_teardown_lifecycle(package_name: str = None) -> str:
    if not package_name:
        return ""
    return f"self.d.app_stop({_format_param_value(package_name)})"


def _wrap_script(body: str, device_type: str = "adb") -> str:
    device_type = _normalize_device_type(device_type)
    
    if device_type == "adb":
        # 处理body可能是字典的情况
        if isinstance(body, dict):
            body_text = body.get('body', '')
            launch_app = body.get('launch_app')
            launch_app_package = body.get('launch_app_package')
        else:
            body_text = body
            launch_app = None
            launch_app_package = None

        setup_body_text = _build_adb_setup_lifecycle(launch_app, launch_app_package)
        teardown_body = _build_adb_teardown_lifecycle(launch_app_package)

        screenshot_wrapper = """
import uiautomator2 as u2
import pytest
import time
import os
from backend.utils.screenshot_collector import ScreenshotCaptureCollector, capture_screenshot, capture_dom_signature, capture_when_stable, wait_until_scroll_idle, wait_until_stable

# 初始化截图收集器
screenshot_collector = ScreenshotCaptureCollector()

class TestAutoGenerated:
    def setup_method(self):
        '''Setup test environment'''
        device_id = os.environ.get('DEVICE_ID')
        if device_id:
            self.d = u2.connect(device_id)
        else:
            self.d = u2.connect()
        self.d.implicitly_wait(15)
{setup_body}

    def test_main(self):
        '''Main test flow'''
{test_body}

    def teardown_method(self):
        '''Cleanup'''
{teardown_body}

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--capture=no"])
""".format(
            setup_body=_indent(setup_body_text, "        ") if setup_body_text else "",
            test_body=_indent(body_text, "        "),
            teardown_body=_indent(teardown_body, "        ") if teardown_body else ""
        )
        return screenshot_wrapper
    else:
        body_str = body.get('body', '') if isinstance(body, dict) else body
        return f"""{_get_framework_imports(device_type)}
def main():
{_indent(body_str, "    ")}

{_get_footer()}"""


def _indent(text: str, prefix: str) -> str:
    return "\n".join(
        f"{prefix}{line}" if line.strip() else line for line in text.split("\n")
    )


def generate_script_from_steps(
    task_text: str, step_results: List[Dict[str, Any]], device_type: str = "adb"
) -> str:
    device_type = _normalize_device_type(device_type)
    draft = framework_template_mapping(step_results or [], device_type=device_type)
    inferred = _infer_body_from_task(task_text, device_type=device_type)
    
    # 处理draft可能是字典的情况
    if isinstance(draft, dict):
        draft_body = draft.get('body', '')
        if inferred:
            if isinstance(inferred, dict):
                inferred_body = inferred.get('body', '')
                if not draft.get('launch_app') and inferred.get('launch_app'):
                    draft['launch_app'] = inferred['launch_app']
                    draft['launch_app_package'] = inferred.get('launch_app_package')
                if not draft.get('close_app') and inferred.get('close_app'):
                    draft['close_app'] = inferred['close_app']
            else:
                inferred_body = inferred
            if inferred_body:
                draft_body = _merge_script_bodies(draft_body, inferred_body)
        draft['body'] = draft_body
    else:
        inferred_body = inferred.get('body', '') if isinstance(inferred, dict) else inferred
        draft = _merge_script_bodies(draft, inferred_body)
    
    if not draft:
        return ""
    if isinstance(draft, dict) and not draft.get('body') and not draft.get('launch_app'):
        return ""
    return _wrap_script(draft, device_type=device_type)


def generate_script_from_task(task_text: str, device_type: str = "adb") -> str:
    device_type = _normalize_device_type(device_type)
    result = _infer_body_from_task(task_text, device_type=device_type)
    if not result:
        return ""
    return _wrap_script(result, device_type=device_type)


# ============ uiautomator2 风格脚本生成 ============
def _build_uiautomator2_call(name: str, args: Dict[str, Any]) -> str:
    """构建 uiautomator2 风格的函数调用（pytest格式，使用self.d）"""
    if name == "click_element":
        by, value = _normalize_click_args(args)
        if by == "resource-id":
            return _build_uiautomator2_selector_click(
                f"resourceId={_format_param_value(value)}"
            )
        elif by == "text":
            return _build_uiautomator2_selector_click(
                f"text={_format_param_value(value)}"
            )
        elif by == "content-desc":
            return _build_uiautomator2_selector_click(
                f"description={_format_param_value(value)}"
            )
        elif by in ("textContains", "textContain"):
            return _build_uiautomator2_selector_click(
                f"textContains={_format_param_value(value)}"
            )
        elif by == "bounds":
            bounds_str = str(value).strip()
            bounds_str = bounds_str.strip("()[]")
            bounds_str = bounds_str.replace("][", ",")
            bounds_str = bounds_str.replace(")-(", ",")
            bounds = [part.strip() for part in bounds_str.split(",") if part.strip()]
            if len(bounds) == 4:
                x = (int(bounds[0]) + int(bounds[2])) // 2
                y = (int(bounds[1]) + int(bounds[3])) // 2
                return f"self.d.click({x}, {y})"
        return _build_uiautomator2_selector_click(f"{by}={_format_param_value(value)}")

    if name == "launch_app":
        app_name = str(args.get("app_name") or args.get("package_name") or "")
        package_name = args.get("package_name") or _lookup_package_name(app_name)
        if package_name:
            return f"self.d.app_start({_format_param_value(package_name)})"
        return f"self.d.app_start({_format_param_value(app_name)})"

    if name == "input_text":
        text = args.get("text", "")
        return f"self.d.send_keys({_format_param_value(text)})"

    if name == "swipe":
        return _build_uiautomator2_swipe_call(args.get("direction", "up"))
    
    if name == "swipe_up":
        return _build_uiautomator2_swipe_call("up")
    
    if name == "swipe_down":
        return _build_uiautomator2_swipe_call("down")
    
    if name == "swipe_left":
        return _build_uiautomator2_swipe_call("left")
    
    if name == "swipe_right":
        return _build_uiautomator2_swipe_call("right")

    if name == "long_press":
        by, value = _normalize_click_args(args)
        duration = args.get("duration", 1000)
        if by == "resource-id":
            return f"self.d(resourceId={_format_param_value(value)}).long_click({duration})"
        elif by == "text":
            return f"self.d(text={_format_param_value(value)}).long_click({duration})"
        elif by == "content-desc":
            return f"self.d(description={_format_param_value(value)}).long_click({duration})"
        elif by in ("textContains", "textContain"):
            return f"self.d(textContains={_format_param_value(value)}).long_click({duration})"
        elif by == "bounds":
            bounds_str = str(value).strip()
            bounds_str = bounds_str.strip("()[]")
            bounds_str = bounds_str.replace("][", ",")
            bounds_str = bounds_str.replace(")-(", ",")
            bounds = [part.strip() for part in bounds_str.split(",") if part.strip()]
            if len(bounds) == 4:
                x = (int(bounds[0]) + int(bounds[2])) // 2
                y = (int(bounds[1]) + int(bounds[3])) // 2
                return f"self.d.long_click({x}, {y}, duration={duration})"
        return f"self.d({by}={_format_param_value(value)}).long_click({duration})"

    if name == "back":
        return "self.d.press('back')"

    if name == "home":
        return "self.d.press('home')"

    if name == "wait":
        duration = args.get("duration", 2.0)
        return f"time.sleep({duration})"

    if name == "close_app":
        app_name = str(args.get("app_name") or args.get("package_name") or "")
        package_name = args.get("package_name")
        target = package_name or app_name
        return f"self.d.app_stop({_format_param_value(target)})"

    return ""


def generate_uiautomator2_script(
    task_text: str, step_results: List[Dict[str, Any]], package_name: str = ""
) -> str:
    """Generate uiautomator2 test script"""
    # Clean task text - remove Chinese characters and emojis to avoid encoding issues
    clean_task_text = re.sub(r'[\u4e00-\u9fff]+', '', task_text)[:30]
    # Remove emojis
    clean_task_text = re.sub(r'[\U00010000-\U0001ffff]+', '', clean_task_text)
    setup_lifecycle = _build_adb_setup_lifecycle(
        f"self.d.app_start({_format_param_value(package_name)})" if package_name else "",
        package_name or None,
    )
    setup_lifecycle_lines = setup_lifecycle.splitlines() if setup_lifecycle else []
    teardown_lifecycle = _build_adb_teardown_lifecycle(package_name or None)
    
    lines = [
        '"""',
        f"Android test: {clean_task_text}" if clean_task_text else "Android test",
        "Requirements: pip install uiautomator2 pytest",
        "Run: python test_script.py",
        '"""',
        "import uiautomator2 as u2",
        "import pytest",
        "import time",
        "import os",
        "from backend.utils.screenshot_collector import wait_until_scroll_idle, wait_until_stable",
        "",
        "class TestAutoGenerated:",
        "    def setup_method(self):",
        '        """Setup test environment"""',
        "        device_id = os.environ.get('DEVICE_ID')",
        "        if device_id:",
        "            self.d = u2.connect(device_id)",
        "        else:",
        "            self.d = u2.connect()",
        "        self.d.implicitly_wait(15)",
        *[f"        {line}" for line in setup_lifecycle_lines],
        "",
        "    def test_main(self):",
        '        """Main test flow"""',
    ]

    step_num = 1
    for step in step_results:
        if not step.get("success"):
            continue
        tool_calls = step.get("tool_calls") or []
        
        for tc in tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "")
            if name == "finish" or name not in SUPPORTED_TOOLS:
                continue
            if name in {"get_ui_tree", "get_current_app", "find_element"}:
                continue
            
            args = _parse_args(func.get("arguments"))
            statement = _build_uiautomator2_call(name, args)
            
            if statement:
                # Skip thinking comments to avoid encoding issues
                for statement_line in statement.splitlines():
                    lines.append(f"        {statement_line}")
                post_wait = _uiautomator2_post_action_wait(name)
                if post_wait:
                    lines.append(f"        {post_wait}")
                elif name == "input_text":
                    lines.append("        time.sleep(1)")
                step_num += 1

    # Verification and cleanup
    lines.extend([
        "",
        "        # Verify task completion",
        "        device_page = self.d(text=\"Device\").exists",
        '        assert device_page, "Task failed"',
        "",
        '        print("Test passed")',
        "",
        "    def teardown_method(self):",
        '        """Cleanup"""',
        *([f"        {teardown_lifecycle}"] if teardown_lifecycle else []),
        "",
        'if __name__ == "__main__":',
        '    pytest.main([__file__, "-v"])',
    ])

    return "\n".join(lines)


# ============ HTML 测试报告生成 ============
def generate_test_report(
    task_text: str,
    step_results: List[Dict[str, Any]],
    package_name: str = "",
    duration: float = 0.0,
    passed: bool = True,
) -> str:
    """生成 HTML 测试报告，类似于 custom_test_report.html"""
    steps_html = ""
    step_num = 1
    for step in step_results:
        if not step.get("success"):
            continue
        tool_calls = step.get("tool_calls") or []
        
        for tc in tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "")
            if name == "finish" or name not in SUPPORTED_TOOLS:
                continue
            if name in {"get_ui_tree", "get_current_app", "find_element"}:
                continue
            
            args = _parse_args(func.get("arguments"))
            
            # 生成步骤标题和描述
            title = ""
            desc = ""
            if name == "click_element":
                by, value = _normalize_click_args(args)
                title = f"点击{value}"
                desc = f"点击元素：{value}"
            elif name == "launch_app":
                title = "启动应用"
                desc = f"启动{args.get('app_name', package_name)}应用"
            elif name == "input_text":
                title = "输入文本"
                desc = "在输入框中输入内容"
            elif name == "swipe":
                title = f"滑动{args.get('direction', 'up')}"
                desc = f"向{args.get('direction', 'up')}方向滑动页面"
            elif name == "close_app":
                title = "关闭应用"
                desc = "关闭当前应用"
            elif name == "wait":
                title = "等待"
                desc = f"等待{args.get('duration', 2)}秒"
            
            if title:
                steps_html += f"""                        <li class="step-item">
                            <div class="step-number">{step_num}</div>
                            <div class="step-content">
                                <h4>{title}</h4>
                                <p>{desc}</p>
                            </div>
                        </li>"""
                step_num += 1

    duration_str = f"{duration:.2f}秒"
    duration_short = f"{duration:.1f}s"
    
    return _get_test_report_template().format(
        title=f"{task_text[:30]}测试报告",
        description="Android自动化测试 - 密码登录流程验证",
        package_name=package_name or "com.lockin.loock",
        framework="pytest + uiautomator2",
        total="1",
        passed="1" if passed else "0",
        failed="0" if passed else "1",
        duration=duration_short,
        duration_full=duration_str,
        status="pass" if passed else "fail",
        steps=steps_html,
    )


# ============ LLM 驱动的脚本生成 ============

# 设备类型到测试框架的映射
DEVICE_FRAMEWORK_MAPPING = {
    "adb": {
        "framework": "pytest + uiautomator2",
        "imports": "import uiautomator2 as u2\nimport pytest\nimport time",
        "device_init": "self.d = u2.connect()",
        "app_start": "self.d.app_start(\"{package_name}\")",
        "app_stop": "self.d.app_stop(\"{package_name}\")",
        "click_by_text": "self.d(text=\"{text}\").click()",
        "click_by_resource_id": "self.d(resourceId=\"{resource_id}\").click()",
        "input_text": "self.d.send_keys(\"{text}\")",
        "wait": "time.sleep({duration})",
        "swipe_up": (
            "width, height = self.d.window_size()\n"
            "self.d.swipe(int(width * 0.5), int(height * 0.8), "
            "int(width * 0.5), int(height * 0.25), duration=0.5)\n"
            "time.sleep(1.5)"
        ),
        "swipe_down": (
            "width, height = self.d.window_size()\n"
            "self.d.swipe(int(width * 0.5), int(height * 0.25), "
            "int(width * 0.5), int(height * 0.8), duration=0.5)\n"
            "time.sleep(1.5)"
        ),
        "press_back": "self.d.press('back')",
        "press_home": "self.d.press('home')",
    },
    "ios": {
        "framework": "pytest + WDA (WebDriverAgent)",
        "imports": "from appium import webdriver\nimport pytest\nimport time",
        "device_init": "self.driver = webdriver.Remote(WDA_URL, desired_caps)",
        "app_start": "self.driver.launch_app()",
        "app_stop": "self.driver.close_app()",
        "click_by_text": "self.driver.find_element('accessibility id', '{text}').click()",
        "click_by_resource_id": "self.driver.find_element('accessibility id', '{resource_id}').click()",
        "input_text": "self.driver.find_element('accessibility id', '{text}').send_keys('{input}')",
        "wait": "time.sleep({duration})",
        "swipe_up": "self.driver.swipe(540, 1500, 540, 500)",
        "swipe_down": "self.driver.swipe(540, 500, 540, 1500)",
        "press_back": "self.driver.back()",
        "press_home": "self.driver.home()",
    },
    "hdc": {
        "framework": "pytest + HDC (HarmonyOS)",
        "imports": "import subprocess\nimport pytest\nimport time",
        "device_init": "# HDC 连接初始化",
        "app_start": "subprocess.run(['hdc', 'shell', 'aa', 'start', '-a', '{app_name}'])",
        "app_stop": "subprocess.run(['hdc', 'shell', 'am', 'force-stop', '{package_name}'])",
        "click_by_text": "# 通过坐标点击文本元素",
        "click_by_resource_id": "# 通过 resource-id 点击",
        "input_text": "subprocess.run(['hdc', 'shell', 'input', 'text', '{text}'])",
        "wait": "time.sleep({duration})",
        "swipe_up": "subprocess.run(['hdc', 'shell', 'input', 'swipe', '540', '1500', '540', '500'])",
        "swipe_down": "subprocess.run(['hdc', 'shell', 'input', 'swipe', '540', '500', '540', '1500'])",
        "press_back": "subprocess.run(['hdc', 'shell', 'input', 'keyevent', 'KEYCODE_BACK'])",
        "press_home": "subprocess.run(['hdc', 'shell', 'input', 'keyevent', 'KEYCODE_HOME'])",
    },
}

# LLM 生成脚本的系统提示词
# UI兼容性断言工具函数模板
def _get_ui_compatibility_assertions() -> str:
    if not hasattr(_get_ui_compatibility_assertions, '_cache'):
        _get_ui_compatibility_assertions._cache = load_template('ui_compatibility_assertions.py')
    return _get_ui_compatibility_assertions._cache


# ============ VLM UI 检查器导入代码 ============
# (已移除 — VLM 分析统一在 Phase 2 通过 VLMUIAnalyzer 延迟执行)

def _build_llm_script_prompt(
    task_text: str,
    step_results: List[Dict[str, Any]],
    device_type: str = "adb",
    with_compatibility_assertions: bool = True
) -> str:
    """构建 LLM 生成脚本的 prompt"""
    framework_info = DEVICE_FRAMEWORK_MAPPING.get(device_type, DEVICE_FRAMEWORK_MAPPING["adb"])
    
    # 构建步骤描述
    steps_description = []
    for i, step in enumerate(step_results, 1):
        if not step.get("success"):
            continue
        thinking = step.get("thinking", "")
        tool_calls = step.get("tool_calls", [])
        
        step_desc = f"步骤 {i}"
        if thinking:
            step_desc += f"：{clean_emoji(thinking)}"
        
        if tool_calls:
            actions = []
            for tc in tool_calls:
                func = tc.get("function", {})
                name = func.get("name", "")
                args = _parse_args(func.get("arguments", "{}"))
                
                if name == "click_element":
                    by = args.get("by", "text")
                    value = args.get("value", "")
                    actions.append(f"点击 {by}={value}")
                elif name == "input_text":
                    text = args.get("text", "")
                    actions.append(f"输入文本：{text}")
                elif name == "launch_app":
                    app_name = args.get("app_name", "")
                    actions.append(f"启动应用：{app_name}")
                elif name == "close_app":
                    actions.append("关闭应用")
                elif name == "swipe":
                    direction = args.get("direction", "up")
                    actions.append(f"滑动：{direction}")
                elif name == "wait":
                    duration = args.get("duration", 2)
                    actions.append(f"等待：{duration}秒")
                elif name == "back":
                    actions.append("按返回键")
                elif name == "home":
                    actions.append("按主页键")
            
            if actions:
                step_desc += f" - {'，'.join(actions)}"
        
        steps_description.append(step_desc)
    
    # 构建脚本要求
    script_requirements = f"""请生成对应的 pytest 测试脚本。

脚本要求：
- 使用 {framework_info['framework']}
- 必须包含以下导入语句：
{chr(10).join(f"- {imp}" for imp in framework_info['imports'].split('\n'))}
- 包含 setup_method（连接设备、启动应用；如果包名明确，启动前先 app_stop 再 app_start）
- 包含测试方法（执行上述步骤）
- 包含 teardown_method（清理环境）
- 添加合理的等待时间和错误处理"""
    
    # 根据参数决定是否添加UI兼容性断言要求
    if with_compatibility_assertions:
        script_requirements += """
- 必须添加 UI 兼容性断言检查，包括：
  - 元素存在性检查（使用 emit_assertion 函数）
  - 文本显示正确性检查
  - 页面状态验证
  - 交互响应验证
- 在关键操作后添加断言，如点击、输入等操作后验证结果"""
    else:
        script_requirements += """
- 不需要添加 UI 兼容性断言或检查点"""
    
    script_requirements += """
- 只输出 Python 代码，不要任何解释

生成脚本：
```python
"""
    
    prompt = f"""任务描述：{task_text}

设备类型：{device_type.upper()}
测试框架：{framework_info['framework']}
必须导入：{framework_info['imports']}

执行步骤：
{chr(10).join(f"{i+1}. {s}" for i, s in enumerate(steps_description))}

{script_requirements}"""
    return prompt


def generate_script_by_llm(
    task_text: str,
    step_results: List[Dict[str, Any]],
    device_type: str = "adb",
    with_compatibility_assertions: bool = True
) -> str:
    """
    使用 LLM 生成符合框架规范的测试脚本

    Args:
        task_text: 任务描述
        step_results: AI Agent 执行步骤结果
        device_type: 设备类型 (adb, ios, hdc)
        with_compatibility_assertions: 是否包含UI兼容性断言（默认 True）

    Returns:
        生成的 Python 测试脚本
    """
    llm = llm_protocols_mod.llm_protocol
    if llm is None:
        # 如果 LLM 未初始化，返回基于模板的脚本
        return _fallback_to_template_generation(step_results, device_type)

    try:
        prompt = _build_llm_script_prompt(task_text, step_results, device_type, with_compatibility_assertions)
        
        messages = [
            {"role": "system", "content": get_script_generator_system_prompt()},
            {"role": "user", "content": prompt}
        ]
        
        result = llm.chat_completion(messages)
        
        if isinstance(result, str) and result and not result.startswith("Error:"):
            # 清理 LLM 输出，移除 markdown 代码块标记
            script = result.strip()
            
            # 移除 markdown 代码块标记
            if script.startswith("```python"):
                script = script[7:]
            if script.startswith("```"):
                script = script[3:]
            if script.endswith("```"):
                script = script[:-3]
            
            script = script.strip()
            
            # 移除开头可能存在的多余字符（如 "on"、"python" 等）
            # 检查脚本是否以有效Python代码开头
            lines = script.split('\n')
            # 找到第一个有效的Python语句行
            first_valid_line_idx = 0
            for i, line in enumerate(lines):
                stripped_line = line.strip()
                # 有效的Python代码开头
                valid_starts = ['import', 'from', 'class', 'def', '#', '"', "'", 'if', 'while', 'for', 'try', 'with']
                if any(stripped_line.startswith(s) for s in valid_starts):
                    first_valid_line_idx = i
                    break
            
            # 只保留从第一个有效行开始的内容
            script = '\n'.join(lines[first_valid_line_idx:]).strip()
            
            # 确保脚本有执行入口
            if "if __name__" not in script:
                # 如果是 pytest 风格脚本，添加 pytest 执行入口
                if "class Test" in script or "def test_" in script:
                    script += "\n\nif __name__ == \"__main__\":\n    pytest.main([__file__, \"-v\"])"
                else:
                    # 如果是普通脚本，添加 main 函数调用
                    script += "\n\nif __name__ == \"__main__\":\n    main()"
            
            return script
        
        # LLM 调用失败，回退到模板生成
        return _fallback_to_template_generation(step_results, device_type)
        
    except Exception as e:
        print(f"LLM 脚本生成失败: {e}")
        return _fallback_to_template_generation(step_results, device_type)


def _fallback_to_template_generation(
    step_results: List[Dict[str, Any]],
    device_type: str = "adb"
) -> str:
    """当 LLM 不可用时，回退到模板生成"""
    device_type = _normalize_device_type(device_type)
    if device_type == "adb":
        body_dict = framework_template_mapping(step_results or [], device_type=device_type)
        return _wrap_script(body_dict, device_type=device_type)
    return generate_uiautomator2_script(
        task_text="自动化测试",
        step_results=step_results,
        package_name=""
    )


def generate_script(
    task_text: str,
    step_results: List[Dict[str, Any]],
    device_type: str = "adb",
    use_llm: bool = True,
    with_compatibility_assertions: bool = True,
    test_type: str = "normal",
    page_changes: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    生成测试脚本的主入口函数

    Args:
        task_text: 任务描述
        step_results: AI Agent 执行步骤结果
        device_type: 设备类型 (adb, ios, hdc)
        use_llm: 是否优先使用 LLM 生成（默认 True）
        with_compatibility_assertions: 是否包含UI兼容性断言（默认 True）
        test_type: 测试类型 ("normal" 普通任务, "ui-compatibility" 兼容性任务)
        page_changes: 页面变化记录（用于智能插入截图埋点），如果不提供则从step_results中提取

    Returns:
        生成的 Python 测试脚本
    """
    valid_steps = [
        s for s in step_results 
        if s.get("success") and s.get("tool_calls")
    ]
    
    # 如果没有提供page_changes，从step_results中提取
    if page_changes is None:
        page_changes = _extract_page_changes_from_steps(step_results)
    
    script_content = ""
    
    if use_llm and llm_protocols_mod.is_llm_initialized() and valid_steps:
        script_content = generate_script_by_llm(task_text, step_results, device_type, with_compatibility_assertions)
    else:
        if not valid_steps and task_text:
            fallback_script = generate_script_from_task(task_text, device_type)
            if fallback_script:
                script_content = fallback_script
        
        if not script_content:
            script_content = _fallback_to_template_generation(step_results, device_type)
    
    if script_content:
        script_content = add_screenshot_captures_to_script(script_content, device_type, page_changes)
    
    return script_content


def _extract_page_changes_from_steps(step_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从步骤结果中提取页面变化记录
    
    Args:
        step_results: 步骤结果列表
    
    Returns:
        页面变化记录列表
    """
    page_changes = []
    
    for step_result in step_results:
        thinking = step_result.get("thinking", "")
        tool_calls = step_result.get("tool_calls", [])
        tool_results = step_result.get("tool_results", [])
        
        # 获取操作名称和参数
        action_name = None
        action_args = {}
        
        if tool_calls:
            for tool_call in tool_calls:
                func = tool_call.get("function", {})
                action_name = func.get("name", "")
                action_args = func.get("arguments", {})
                break
        
        # 从工具结果中提取页面变化信息
        if tool_results and action_name:
            for tool_result in tool_results:
                if isinstance(tool_result, dict) and "_page_change" in tool_result:
                    page_change_info = tool_result["_page_change"]
                    page_changes.append({
                        "action_name": action_name,
                        "action_args": action_args,
                        "thinking": thinking,
                        "before_hash": page_change_info.get("before_hash"),
                        "after_hash": page_change_info.get("after_hash"),
                        "page_changed": page_change_info.get("page_changed", False),
                    })
                    break
    
    return page_changes

