"""MCP 工具定义 - OpenAI Function Calling 格式"""

import json
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from backend.mcp.mcp_tools_base import UITreeResult

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_ui_tree",
            "description": "获取当前屏幕的 UI 元素树，返回所有可交互元素的列表",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_element",
            "description": "根据指定条件查找 UI 元素",
            "parameters": {
                "type": "object",
                "properties": {
                    "by": {
                        "type": "string",
                        "enum": [
                            "text",
                            "textContains",
                            "textContain",
                            "resource-id",
                            "resource_id",
                            "content-desc",
                            "xpath",
                            "class",
                        ],
                        "description": "元素定位方式",
                    },
                    "value": {"type": "string", "description": "定位值"},
                    "timeout": {
                        "type": "number",
                        "description": "等待超时（秒）",
                        "default": 0,
                    },
                },
                "required": ["by", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_elements",
            "description": "查找所有匹配指定条件的 UI 元素",
            "parameters": {
                "type": "object",
                "properties": {
                    "by": {
                        "type": "string",
                        "enum": [
                            "text",
                            "textContains",
                            "textContain",
                            "resource-id",
                            "resource_id",
                            "content-desc",
                            "xpath",
                            "class",
                        ],
                        "description": "元素定位方式",
                    },
                    "value": {"type": "string", "description": "定位值"},
                },
                "required": ["by", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_element",
            "description": "点击自然语言可定位的 UI 元素。用于“点击某某按钮/文本”等语义点击；不要用于纯坐标点击",
            "parameters": {
                "type": "object",
                "properties": {
                    "by": {
                        "type": "string",
                        "enum": [
                            "text",
                            "textContains",
                            "textContain",
                            "resource-id",
                            "resource_id",
                            "content-desc",
                            "xpath",
                            "bounds",
                        ],
                        "description": "元素定位方式",
                    },
                    "value": {
                        "type": "string",
                        "description": "定位值；bounds 格式如 [100,200][300,260]",
                    },
                },
                "required": ["by", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tap",
            "description": "按屏幕坐标点击。仅当已经明确知道坐标时使用；点击可见文本或按钮时优先使用 click_element",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "屏幕横坐标"},
                    "y": {"type": "integer", "description": "屏幕纵坐标"},
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_element_with_fallback",
            "description": "使用多种策略点击元素，当主策略失败时自动尝试备选策略。适用于元素定位不稳定的情况",
            "parameters": {
                "type": "object",
                "properties": {
                    "strategies": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "by": {
                                    "type": "string",
                                    "enum": [
                                        "text",
                                        "textContains",
                                        "textContain",
                                        "resource-id",
                                        "resource_id",
                                        "content-desc",
                                        "xpath",
                                    ],
                                    "description": "元素定位方式",
                                },
                                "value": {"type": "string", "description": "定位值"},
                            },
                            "required": ["by", "value"],
                        },
                        "description": "定位策略列表，按优先级排序。先尝试前面的策略，失败则自动尝试下一个",
                    }
                },
                "required": ["strategies"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "input_text",
            "description": "在当前聚焦的输入框中输入文本",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要输入的文本内容"},
                    "clear_first": {
                        "type": "boolean",
                        "description": "是否先清空输入框",
                        "default": True,
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "swipe",
            "description": "执行滑动操作",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down", "left", "right"],
                        "description": "滑动方向",
                    },
                    "distance": {
                        "type": "string",
                        "description": "滑动距离比例，如 '50%'",
                        "default": "50%",
                    },
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "long_press",
            "description": "长按指定元素",
            "parameters": {
                "type": "object",
                "properties": {
                    "by": {"type": "string", "description": "元素定位方式"},
                    "value": {"type": "string", "description": "定位值"},
                    "duration": {
                        "type": "integer",
                        "description": "长按时长（毫秒）",
                        "default": 1000,
                    },
                },
                "required": ["by", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "back",
            "description": "执行返回操作",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "home",
            "description": "回到桌面",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_app",
            "description": "启动指定应用",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "应用名称或包名"},
                    "package_name": {
                        "type": "string",
                        "description": "应用包名（可选）",
                    },
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_app",
            "description": "关闭指定应用（强制停止应用进程）",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "应用名称或包名"},
                    "package_name": {
                        "type": "string",
                        "description": "应用包名（可选）",
                    },
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_app",
            "description": "获取当前前台应用信息",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "等待指定时间",
            "parameters": {
                "type": "object",
                "properties": {
                    "duration": {
                        "type": "number",
                        "description": "等待时长（秒）",
                        "default": 2.0,
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_element",
            "description": "按压元素（用于确认等场景）",
            "parameters": {
                "type": "object",
                "properties": {
                    "by": {"type": "string", "description": "元素定位方式"},
                    "value": {"type": "string", "description": "定位值"},
                },
                "required": ["by", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "完成任务，返回结果",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "任务完成信息"},
                    "success": {
                        "type": "boolean",
                        "description": "是否成功完成",
                        "default": True,
                    },
                },
                "required": ["message"],
            },
        },
    },
]


SYSTEM_PROMPT = """你是一个智能设备操作助手，通过分析 UI 元素信息来执行用户任务。

# 工具使用规则

你可以使用以下工具来操作设备：
- get_ui_tree: 获取当前屏幕所有 UI 元素
- find_element / find_elements: 查找特定元素
- click_element: 点击自然语言可定位的元素，如文本、包含文本、资源 ID、描述或 bounds
- tap: 点击明确坐标，只在已经知道 x/y 时使用
- input_text: 输入文本
- swipe: 滑动屏幕
- long_press: 长按元素
- back: 返回上一页
- home: 回到桌面
- launch_app: 启动应用
- close_app: 关闭应用（强制停止应用进程）
- get_current_app: 获取当前应用
- wait: 等待
- finish: 完成任务

# 智能决策规则（重要）

## 状态感知（点击前检查）
- **Checkbox/Switch 元素**：点击前必须检查元素的 `checked` 属性
  - 如果 `checked="true"`，说明已选中，**跳过点击**，直接进入下一步
  - 如果 `checked="false"`，说明未选中，执行点击
  - 点击 checkbox 会切换状态，重复点击会导致取消选中！
  
- **输入框元素**：检查是否已有内容
  - 如果输入框已有目标文本，**跳过输入**
  - 如果需要清空，使用 `clear_first: true`

## 记忆与学习
- **记住成功经验**：如果某个操作序列成功完成了任务，下次遇到相同场景应复用
- **避免重复失败**：如果某个操作之前失败过，不要重复尝试相同的参数
- **从错误中学习**：如果点击某个元素导致意外结果（如取消选中），下次应避免

## 决策优化
- 每次操作前思考：这个操作是否必要？元素是否已处于目标状态？
- 避免冗余操作：不要重复点击已选中的 checkbox，不要重复输入已存在的文本
- 操作历史分析：回顾之前的步骤，避免重复或无效操作

# 操作流程

1. 根据任务目标，规划下一步操作
2. **检查元素状态**：在点击/输入前，确认元素当前状态
3. 如果任务是"打开XX应用，点击YY"，先调用 launch_app，然后调用 click_element(by="text", value="YY") 或使用其他语义定位方式
4. 只有用户明确给出坐标或视觉模型已经确定坐标时，才调用 tap(x, y)
5. 如果 click_element 定位失败，再调用 get_ui_tree / find_element 分析页面并重试其他语义定位方式
6. 检查操作结果，决定是否继续
7. 完成任务后调用 finish

# 输入操作最佳实践

## 密码输入（强制规则）
- **禁止**使用 tap 逐字符输入密码，这会浪费大量步骤
- **必须**直接使用 input_text 方法输入密码，即使是安全键盘场景
- input_text 方法会自动处理安全键盘、系统键盘等各种输入场景
- 输入格式：input_text({"text": "密码内容", "clear_first": true})
- **注意**：不需要先点击密码输入框再输入，input_text 会自动定位并聚焦输入框

## 文本输入（强制规则）
- 在任何输入框中输入文本时，**必须**直接使用 input_text 方法
- **禁止**先点击输入框再输入（除非 input_text 失败）
- 如果输入失败，可以先点击输入框获得焦点，然后再重试 input_text

# 元素定位优先级

优先使用以下定位方式（按可靠性排序）：
1. resource-id（最可靠）
2. text（精确匹配）
3. content-desc
4. textContains（部分匹配）
5. xpath（复杂场景）
6. bounds（已知元素边界时使用）

# 任务完成判断

**重要：**
- 如果任务只是"打开XX应用"，在调用 launch_app 后，使用 get_current_app 确认目标应用已启动即可完成任务
- 如果 get_current_app 返回的包名与目标应用匹配，说明应用已成功打开，可直接调用 finish
- 无需获取完整 UI 树来确认应用已打开，除非任务需要进一步的 UI 交互

# 输出格式

每一步必须输出：
[思考]: [分析当前状态，规划下一步]
[工具调用]: [tool_name(params)]
[结果]: [工具返回结果]

# 注意事项

- 执行操作前先确认元素存在
- **执行操作前检查元素状态（checkbox 是否已选中、输入框是否已有内容）**
- 操作失败时尝试其他定位方式
- UI 树连续为空或多种定位方式都失败时，不要反复等待；返回失败并说明需要 VLM 视觉降级
- 自然语言动作提示只是候选计划，不是平台已完成的动作，也不是页面证据；每一步必须由你基于当前 UI、操作历史和工具结果重新决策
- 当 UI 树为空、截图不可用、或上下文仅包含 current_app 时，不得断言页面上存在具体按钮、tab、输入框或文案
- 文本点击任务必须优先使用 UI 树/uiautomator2 文本定位工具；只有定位失败且说明失败原因后，才允许使用坐标 fallback
- 思考过程必须区分"已观察到"和"推测"。未被 UI 树或截图证实的信息必须明确标记为推测
- 只有在所有用户目标都已被观察或工具结果证明完成后，才允许调用 finish；不能因为动作提示列表为空就结束任务
- 如果用户纠正上一轮判断，下一轮必须显式采纳纠正，避免重复同一错误判断或同一坐标猜测
- 遇到弹窗先处理弹窗
- 不确定时可以先等待再重试
- 最大步数限制为 100 步"""


USER_PROMPT_TEMPLATE = """任务: {task}

当前日期: {current_date}

当前 UI 状态:
{ui_tree_text}

操作历史:
{history_text}

请分析当前状态，决定下一步操作。"""


def format_history(history: List[Dict[str, Any]]) -> str:
    """格式化操作历史"""
    if not history:
        return "（无操作历史）"

    lines = []
    for i, item in enumerate(history):
        step = item.get("step", i + 1)
        tool_calls = item.get("tool_calls", [])
        tool_results = item.get("tool_results", [])

        # 处理新的历史格式（包含 tool_calls 和 tool_results）
        if tool_calls:
            for j, tc in enumerate(tool_calls):
                func = tc.get("function", {})
                tool_name = func.get("name", "unknown")
                args_str = func.get("arguments", "{}")
                try:
                    args = (
                        json.loads(args_str) if isinstance(args_str, str) else args_str
                    )
                except (TypeError, json.JSONDecodeError):
                    args = {}

                # 获取对应的结果
                result = {}
                if j < len(tool_results):
                    result = tool_results[j]
                    # 解析 tool_message 格式
                    if "content" in result:
                        try:
                            content = result.get("content", "")
                            if isinstance(content, str):
                                result = json.loads(content)
                        except (TypeError, json.JSONDecodeError):
                            pass

                lines.append(f"[Step {step}] {tool_name}({args})")
                if isinstance(result, dict):
                    if result.get("success"):
                        msg = result.get("message", "成功")
                        lines.append(f"    ✅ {msg}")
                    else:
                        err = result.get("error", result.get("message", "失败"))
                        lines.append(f"    ❌ {err}")
                else:
                    lines.append(f"    结果: {result}")
        else:
            # 兼容旧格式
            tool = item.get("tool", "unknown")
            args = item.get("args", {})
            result = item.get("result", {})
            lines.append(f"[Step {step}] {tool}({args})")
            if result.get("success"):
                lines.append(f"    ✅ {result.get('message', '成功')}")
            else:
                lines.append(f"    ❌ {result.get('message', '失败')}")

    return "\n".join(lines)


def build_user_prompt(
    task: str, ui_tree: "UITreeResult", history: List[Dict[str, Any]]
) -> str:
    """构建用户 Prompt"""

    from datetime import datetime
    
    ui_text = ui_tree.format_text() if hasattr(ui_tree, "format_text") else str(ui_tree)
    history_text = format_history(history)
    current_date = datetime.now().strftime("%Y-%m-%d")

    return USER_PROMPT_TEMPLATE.format(
        task=task, current_date=current_date, ui_tree_text=ui_text, history_text=history_text
    )
