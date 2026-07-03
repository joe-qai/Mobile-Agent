"""LLM协议适配层 - 支持openapi和anthropic双协议，含工具调用支持"""

import json
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Union

import httpx


class LLMProtocol(ABC):
    """LLM协议抽象基类"""

    def __init__(self, base_url: str, apikey: str, model: str):
        if not base_url.startswith("http://") and not base_url.startswith("https://"):
            base_url = "https://" + base_url
        self.base_url = base_url.rstrip("/")
        self.apikey = apikey
        self.model = model

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        stream: bool = False
    ) -> Union[str, Dict[str, Any]]:
        """执行聊天补全"""
        raise NotImplementedError

    def stream_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """流式聊天补全"""
        raise NotImplementedError


class OpenAIProtocol(LLMProtocol):
    """OpenAI协议实现（支持 function calling）"""

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        stream: bool = False
    ) -> Union[str, Dict[str, Any]]:
        """执行聊天补全（支持 function calling）"""
        headers = {
            "Authorization": f"Bearer {self.apikey}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "stream": False,  # 明确禁用流式输出
        }

        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = {"type": "function", "function": {"name": tool_choice}}

        try:
            if stream:
                return self._stream_completion(headers, payload)
            else:
                return self._non_stream_completion(headers, payload)
        except Exception as e:
            return f"Error: {str(e)}"

    def _non_stream_completion(
        self,
        headers: Dict[str, str],
        payload: Dict[str, Any]
    ) -> Union[str, Dict[str, Any]]:
        """非流式补全"""
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=120,
            )
            
            # 检查 HTTP 状态码
            if response.status_code != 200:
                return f"Error: HTTP {response.status_code} - {response.text[:200]}"
            
            # 检查响应内容是否为空
            if not response.text:
                return f"Error: 空响应"
            
            # 尝试解析 JSON
            try:
                result = response.json()
            except json.JSONDecodeError as e:
                return f"Error: JSON解析失败 - 响应内容: {response.text[:200]}"
            
            if result.get("choices") and len(result["choices"]) > 0:
                choice = result["choices"][0]
                message = choice.get("message", {})

                if message.get("tool_calls"):
                    return {
                        "role": message.get("role"),
                        "content": message.get("content", ""),
                        "tool_calls": message.get("tool_calls")
                    }

                return message.get("content", "")

            # 检查是否有错误信息
            if "error" in result:
                error_info = result.get("error", {})
                return f"Error: {error_info.get('message', str(error_info))}"

            return ""
            
        except httpx.TimeoutException:
            return f"Error: 请求超时"
        except httpx.RequestError as e:
            return f"Error: 网络请求失败 - {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    def _stream_completion(
        self,
        headers: Dict[str, str],
        payload: Dict[str, Any]
    ) -> str:
        """流式补全"""
        payload["stream"] = True
        full_response = ""

        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=120,
            ) as response:
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            if "choices" in chunk and chunk["choices"]:
                                content = chunk["choices"][0]["delta"].get("content", "")
                                full_response += content
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            return f"Error: {str(e)}"

        return full_response

    def stream_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """流式聊天补全"""
        return self.chat_completion(messages, tools=tools, stream=True)


class AnthropicProtocol(LLMProtocol):
    """Anthropic协议实现"""

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        stream: bool = False
    ) -> Union[str, Dict[str, Any]]:
        """执行聊天补全"""
        headers = {
            "x-api-key": self.apikey,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }

        system_message = ""
        user_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                user_messages.append({"role": msg["role"], "content": msg["content"]})

        payload: Dict[str, Any] = {
            "model": self.model,
            "system": system_message,
            "messages": user_messages,
            "max_tokens": 4096,
            "temperature": 0.7,
        }

        if tools:
            payload["tools"] = self._convert_tools_to_anthropic(tools)

        try:
            if stream:
                return self._stream_completion_anthropic(headers, payload)
            else:
                return self._non_stream_completion_anthropic(headers, payload)
        except Exception as e:
            return f"Error: {str(e)}"

    def _non_stream_completion_anthropic(
        self,
        headers: Dict[str, str],
        payload: Dict[str, Any]
    ) -> Union[str, Dict[str, Any]]:
        """Anthropic 非流式补全"""
        response = httpx.post(
            f"{self.base_url}/messages", headers=headers, json=payload, timeout=120
        )
        response.raise_for_status()
        result = response.json()

        if result.get("content") and len(result["content"]) > 0:
            content = result["content"][0]
            if content.get("type") == "tool_use":
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": content.get("id", ""),
                        "function": {
                            "name": content.get("name", ""),
                            "arguments": json.dumps(content.get("input", {}))
                        }
                    }]
                }
            return content.get("text", "")

        return ""

    def _stream_completion_anthropic(
        self,
        headers: Dict[str, str],
        payload: Dict[str, Any]
    ) -> str:
        """Anthropic 流式补全"""
        payload["stream"] = True
        full_response = ""

        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/messages",
                headers=headers,
                json=payload,
                timeout=120,
            ) as response:
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        try:
                            chunk = json.loads(data)
                            if chunk.get("type") == "content_block_delta":
                                content = chunk.get("delta", {}).get("text", "")
                                full_response += content
                            elif chunk.get("type") == "message_stop":
                                break
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            return f"Error: {str(e)}"

        return full_response

    def _convert_tools_to_anthropic(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将 OpenAI 格式工具转换为 Anthropic 格式"""
        anthropic_tools = []
        for tool in tools:
            func = tool.get("function", {})
            anthropic_tools.append({
                "name": func.get("name"),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {})
            })
        return anthropic_tools

    def stream_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """流式聊天补全"""
        return self.chat_completion(messages, tools=tools, stream=True)


class ReActAgentLLMClient:
    """
    ReAct Agent 专用 LLM 客户端
    支持 function calling 和结构化输出
    """

    def __init__(
        self,
        llm_protocol: LLMProtocol,
        tools: Optional[List[Dict[str, Any]]] = None,
        log_callback: Optional[Callable[[str], None]] = None
    ):
        self.llm_protocol = llm_protocol
        self.tools = tools or []
        self.log_callback = log_callback

    def log(self, message: str):
        """输出日志"""
        if self.log_callback:
            self.log_callback(message)

    def chat(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        require_tool_call: bool = False
    ) -> Dict[str, Any]:
        """
        发送聊天请求

        Args:
            messages: 消息列表
            system_prompt: 系统提示（可选，会被添加到 messages）
            require_tool_call: 是否强制要求工具调用

        Returns:
            {
                "content": str,  # 文本内容
                "tool_calls": List[Dict],  # 工具调用列表
                "finish_reason": str  # 结束原因 (stop/tool_calls)
            }
        """
        final_messages = []

        if system_prompt:
            final_messages.append({"role": "system", "content": system_prompt})

        final_messages.extend(messages)

        self.log(f"[LLM] 发送请求，消息数: {len(final_messages)}, 工具数: {len(self.tools)}")

        response = self.llm_protocol.chat_completion(
            messages=final_messages,
            tools=self.tools if self.tools else None
        )

        if isinstance(response, str):
            if response.startswith("Error:"):
                self.log(f"[LLM] 请求错误: {response}")
                return {"content": response, "tool_calls": [], "finish_reason": "error"}

            self.log(f"[LLM] 响应: {response[:200]}...")
            return {"content": response, "tool_calls": [], "finish_reason": "stop"}

        tool_calls = response.get("tool_calls", [])
        content = response.get("content", "")

        if tool_calls:
            self.log(f"[LLM] 工具调用: {[tc['function']['name'] for tc in tool_calls]}")
            return {
                "content": content,
                "tool_calls": tool_calls,
                "finish_reason": "tool_calls"
            }

        return {"content": content, "tool_calls": [], "finish_reason": "stop"}

    def parse_tool_result(self, tool_call_id: str, function_name: str, result: Any) -> Dict[str, Any]:
        """将工具执行结果转换为消息格式"""
        if hasattr(result, 'to_dict'):
            content = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
        elif isinstance(result, dict):
            content = json.dumps(result, ensure_ascii=False, indent=2)
        else:
            content = str(result)

        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": function_name,
            "content": content
        }


# 全局 LLM 协议实例
llm_protocol: Optional[LLMProtocol] = None


def is_llm_initialized() -> bool:
    """检查 LLM 是否已初始化"""
    return llm_protocol is not None


def init_llm(protocol: str, base_url: str, apikey: str, model: str) -> bool:
    """
    初始化 LLM 协议

    Args:
        protocol: 协议类型 (openapi, anthropic)
        base_url: API 基础 URL
        apikey: API 密钥
        model: 模型名称

    Returns:
        是否初始化成功
    """
    global llm_protocol

    try:
        if protocol == "openapi" or protocol == "openai":
            llm_protocol = OpenAIProtocol(base_url, apikey, model)
        elif protocol == "anthropic":
            llm_protocol = AnthropicProtocol(base_url, apikey, model)
        else:
            return False

        return True
    except Exception as e:
        print(f"LLM 初始化失败: {e}")
        return False


class LLMProtocolFactory:
    """LLM 协议工厂类"""

    @staticmethod
    def create_protocol(protocol: str, base_url: str, apikey: str, model: str) -> Optional[LLMProtocol]:
        """
        创建 LLM 协议实例

        Args:
            protocol: 协议类型 (openapi, anthropic)
            base_url: API 基础 URL
            apikey: API 密钥
            model: 模型名称

        Returns:
            LLMProtocol 实例，失败返回 None
        """
        try:
            if protocol == "openapi" or protocol == "openai":
                return OpenAIProtocol(base_url, apikey, model)
            elif protocol == "anthropic":
                return AnthropicProtocol(base_url, apikey, model)
            else:
                return None
        except Exception:
            return None


class _OpenAICompatibleClient:
    """OpenAI SDK 风格兼容客户端适配器"""

    def __init__(self, protocol: LLMProtocol):
        self.protocol = protocol
        self.chat = _OpenAICompatibleChat(protocol)


class _OpenAICompatibleChat:
    """OpenAI SDK 风格的 chat 模块"""

    def __init__(self, protocol: LLMProtocol):
        self.protocol = protocol
        self.completions = _OpenAICompatibleCompletions(protocol)


class _OpenAICompatibleCompletions:
    """OpenAI SDK 风格的 completions 模块"""

    def __init__(self, protocol: LLMProtocol):
        self.protocol = protocol

    def create(self, model: str, messages: List[Dict[str, Any]], max_tokens: int = 512) -> Any:
        """
        OpenAI SDK 风格的 create 方法

        Args:
            model: 模型名称
            messages: 消息列表
            max_tokens: 最大 tokens

        Returns:
            OpenAI SDK 风格的响应对象
        """
        # 临时修改 protocol 的 model（如果传入了不同的 model）
        original_model = self.protocol.model
        if model:
            self.protocol.model = model

        try:
            response = self.protocol.chat_completion(messages=messages, stream=False)

            if isinstance(response, str):
                if response.startswith("Error:"):
                    raise Exception(response)
                content = response
            elif isinstance(response, dict) and response.get("content"):
                content = response["content"]
            else:
                content = str(response)

            return _OpenAICompatibleResponse(content)
        finally:
            self.protocol.model = original_model


class _OpenAICompatibleResponse:
    """OpenAI SDK 风格的响应对象"""

    def __init__(self, content: str):
        self.choices = [_OpenAICompatibleChoice(content)]


class _OpenAICompatibleChoice:
    """OpenAI SDK 风格的 choice 对象"""

    def __init__(self, content: str):
        self.message = _OpenAICompatibleMessage(content)


class _OpenAICompatibleMessage:
    """OpenAI SDK 风格的 message 对象"""

    def __init__(self, content: str):
        self.content = content


def create_llm_client(protocol: str, base_url: str, apikey: str, model: str = "") -> _OpenAICompatibleClient:
    """
    创建 LLM 客户端（OpenAI SDK 风格接口）

    Args:
        protocol: 协议类型 (openapi, anthropic)
        base_url: API 基础 URL
        apikey: API 密钥
        model: 模型名称

    Returns:
        兼容 OpenAI SDK 风格的客户端对象
    """
    llm_protocol = LLMProtocolFactory.create_protocol(protocol, base_url, apikey, model or "glm-5.1")
    if not llm_protocol:
        raise ValueError(f"不支持的协议类型: {protocol}")
    return _OpenAICompatibleClient(llm_protocol)
