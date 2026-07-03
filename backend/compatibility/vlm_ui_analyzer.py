"""
VLM UI 分析器（VLM UI Analyzer）
- 使用 VLM 分析 UI 兼容性问题
- 生成标准断言事件
- 支持多维度 UI 检测
- 设备上下文采集（型号、系统版本、分辨率、主题模式）
- Blocker级问题二次确认机制
- 性能优化：请求缓存、限流控制、重试机制
"""

import base64
import json
import logging
import os
from typing import Any, Dict, List

import httpx

from .vlm_performance import get_global_vlm_cache

logger = logging.getLogger(__name__)


class VLMUIAnalyzer:
    """
    VLM UI 分析器：使用视觉语言模型分析 UI 兼容性
    
    功能：
    - 分析截图中的 UI 兼容性问题
    - 生成标准断言事件
    - 支持多维度检测（布局、文字、图片、交互等）
    - 设备上下文采集（型号、系统版本、分辨率、主题模式）
    - Blocker级问题二次确认机制
    """
    
    # 完整版提示词（包含设备上下文）
    VLM_UI_ANALYSIS_PROMPT = """
请分析以下移动端应用截图的 UI 兼容性问题：

【设备上下文】
设备型号：{device_model}
系统版本：{os_version}
屏幕分辨率：{resolution}
主题模式：{theme}

【页面信息】
当前页面：{page_name}

【测试维度】
{dimensions}

请按以下格式输出分析结果（JSON格式）：

{{
  "page_id": "{page_name}",
  "overall_assessment": "pass|warning|fail",
  "confidence": 0.0,
  "issues": [
    {{
      "category": "layout|text|image|adaptation|theme|page_state",
      "subtype": "string",
      "severity": "blocker|major|minor|suggestion",
      "description": "详细描述问题",
      "location": "问题位置描述",
      "bbox": [x_center, y_center, width, height],
      "suggestion": "修复建议"
    }}
  ]
}}

【坐标要求】
**每个 issue 必须包含 "bbox" 字段，这是强制性要求，缺少 bbox 的 issue 将被忽略！**

格式：归一化坐标 [x_center, y_center, width, height]，值域 0~1。
- 以截图左上角为原点 (0,0)，右下角为 (1,1)
- x_center, y_center 是问题区域中心点的相对坐标
- width, height 是问题区域的相对宽高
- 例如：居中按钮的 bbox 为 [0.5, 0.3, 0.3, 0.08]
- 对于文字截断问题：bbox 应覆盖被截断的文字区域
- 对于布局错位问题：bbox 应覆盖错位的元素
- 对于按钮不可点击问题：bbox 应覆盖该按钮区域

【维度说明】
- layout: 布局检查（元素重叠、遮挡、错位、溢出、间距问题）
- text: 文字检查（截断、乱码、显示不全、字体大小、对比度）
- image: 图片检查（拉伸、变形、加载失败、裁剪不当）
- adaptation: 设备适配（异形屏遮挡、屏幕方向、大屏适配）
- theme: 主题适配（深色模式对比度、不可见元素）
- page_state: 页面状态（白屏、加载中不消失、内容闪烁、空白内容、元素重复）

【严重程度说明】
- blocker: 严重阻塞问题，功能无法使用
- major: 主要问题，影响用户体验
- minor: 次要问题，轻微影响
- suggestion: 建议优化

请仔细分析截图，找出所有 UI 兼容性问题，输出 JSON 格式的分析结果。

【重要】必须直接输出纯 JSON，不要用 markdown 代码块包裹（不要写 ```json 或 ```），不要在 JSON 前后添加任何文字说明。只输出一个完整的 JSON 对象。
"""
    
    # 二次确认提示词（针对Blocker级问题）
    VLM_BLOCKER_CONFIRM_PROMPT = """
请对以下 UI 问题进行二次确认：

【问题描述】
{issue_description}

【设备上下文】
设备型号：{device_model}
系统版本：{os_version}
屏幕分辨率：{resolution}
主题模式：{theme}

请确认以下问题：
1. 该问题是否真实存在？
2. 严重程度是否为 blocker（严重阻塞）？
3. 请提供问题位置的详细描述

请按以下 JSON 格式输出确认结果：

{{
  "confirmed": true|false,
  "confidence": 0.0,
  "severity": "blocker|major|minor|suggestion",
   "location": "问题位置",
   "bbox": [x_center, y_center, width, height],
   "additional_notes": "补充说明（可选）"
}}

【重要】必须直接输出纯 JSON，不要用 markdown 代码块包裹（不要写 ```json 或 ```），不要在 JSON 前后添加任何文字说明。只输出一个完整的 JSON 对象。
"""
    
    # 默认检测维度
    DEFAULT_DIMENSIONS = [
        "layout", "text", "image",
        "adaptation", "theme", "page_state"
    ]
    
    def __init__(
        self,
        base_url: str = None,
        model: str = None,
        api_key: str = None,
    ):
        """
        初始化 VLM UI 分析器
        
        Args:
            base_url: VLM API 地址
            model: 模型名称
        """
        config = self._load_runtime_config()
        resolved_base_url = (
            base_url
            or config.get("base_url")
            or "http://localhost:8000/v1"
        )
        self.timeout_seconds = self._coerce_timeout(config.get("timeout"), default=300.0)
        self.connect_timeout_seconds = float(os.environ.get("VLM_CONNECT_TIMEOUT", "10.0"))
        self.base_url = resolved_base_url.rstrip("/")
        self.model = model or config.get("model") or "glm-4v"
        self.api_key = api_key or config.get("api_key") or ""
        # 重试配置
        # 默认不重试（超时后直接返回错误）
        self.max_retries = int(os.environ.get("VLM_MAX_RETRIES", "0"))
        self.retry_delay = float(os.environ.get("VLM_RETRY_DELAY", "5.0"))
        self.client = httpx.Client(
            timeout=httpx.Timeout(
                self.timeout_seconds,
                connect=self.connect_timeout_seconds,
                read=self.timeout_seconds,
            ),
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
            ),
        )
        self.default_device_context = {
            "platform": "Android",
            "device_model": "unknown",
            "os_version": "unknown",
            "resolution": "unknown",
            "theme": "light"
        }

    def _load_runtime_config(self) -> Dict[str, str]:
        """Load VLM config from DB first, then environment fallbacks."""
        config = {
            "base_url": os.environ.get("VLM_BASE_URL"),
            "model": os.environ.get("VLM_MODEL"),
            "api_key": os.environ.get("VLM_API_KEY")
            or os.environ.get("OPENAI_API_KEY"),
            "timeout": os.environ.get("VLM_TIMEOUT"),
        }
        try:
            from backend.db.database import get_config

            config["base_url"] = (
                get_config("vlm_base_url", "")
                or get_config("llm_base_url", "")
                or config.get("base_url")
            )
            config["model"] = (
                get_config("vlm_model", "")
                or get_config("llm_model", "")
                or config.get("model")
            )
            config["api_key"] = (
                get_config("vlm_apikey", "")
                or get_config("llm_apikey", "")
                or config.get("api_key")
            )
            config["timeout"] = (
                get_config("vlm_timeout", "")
                or get_config("llm_timeout", "")
                or config.get("timeout")
            )
        except Exception as exc:
            logger.debug("加载 VLM 配置失败，使用环境变量/默认值: %s", exc)
        return {key: value for key, value in config.items() if value}

    @staticmethod
    def _coerce_timeout(value: Any, default: float) -> float:
        try:
            timeout = float(value)
        except (TypeError, ValueError):
            timeout = default
        return max(3.0, min(timeout, 600.0))  # 上限600秒
    
    def analyze_screen(
        self,
        screenshot_base64: str,
        context: Dict[str, Any],
        dimensions: List[str] = None
    ) -> Dict[str, Any]:
        """
        分析屏幕截图（主入口）
        
        Args:
            screenshot_base64: 截图的 base64 编码
            context: 上下文信息（page_name, device 等）
            dimensions: 检测维度列表
        
        Returns:
            分析结果字典
        """
        dimensions = dimensions or self.DEFAULT_DIMENSIONS
        
        # 提取设备上下文
        device_context = context.get("device", self.default_device_context)
        page_name = context.get("page_name", "未知页面")
        page_description = context.get("page_description", "")
        target = context.get("target", "")
        
        # 构建提示词（包含设备上下文）
        prompt = self._build_prompt(
            page_name,
            dimensions,
            device_context,
            page_description=page_description,
            target=target,
        )
        
        # 调用 VLM
        try:
            response = self._call_vlm(screenshot_base64, prompt)
            
            # VLM 请求超时或 JSON 解析失败时标记人工审核
            if response.get("error") and "needs_manual_review" not in response:
                error_msg = response.get("error", "")
                is_timeout_or_parse = (
                    "超时" in error_msg
                    or "timeout" in error_msg.lower()
                    or "无法解析 JSON" in error_msg
                    or "JSON 解析失败" in error_msg
                    or "HTTP错误" in error_msg
                    or "空内容" in error_msg
                )
                if is_timeout_or_parse:
                    response["needs_manual_review"] = True
            
            # 对Blocker级问题进行二次确认
            response = self._confirm_blocker_issues(
                response, screenshot_base64, device_context
            )
            
            return response
        except Exception as e:
            logger.error(f"VLM 分析失败: {e}")
            return self._get_default_result(str(e), page_name)
    
    def _confirm_blocker_issues(
        self,
        analysis_result: Dict[str, Any],
        screenshot_base64: str,
        device_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        对Blocker级问题进行二次确认
        
        Args:
            analysis_result: 初始分析结果
            screenshot_base64: 截图的 base64 编码
            device_context: 设备上下文
        
        Returns:
            确认后的分析结果
        """
        issues = analysis_result.get("issues", [])
        confirmed_issues = []
        
        for issue in issues:
            severity = issue.get("severity", "")
            
            if severity == "blocker":
                # 需要二次确认
                confirmed = self._confirm_single_issue(
                    issue, screenshot_base64, device_context
                )
                
                # 二次确认请求超时或 JSON 解析失败 → 保留首次分析结果，不做降级
                if confirmed.get("error") and ("超时" in confirmed["error"] or "无法解析 JSON" in confirmed["error"] or "timeout" in confirmed["error"].lower()):
                    issue["confidence"] = issue.get("confidence", 0.7)
                    confirmed_issues.append(issue)
                elif confirmed.get("confirmed", False):
                    # 确认存在，更新置信度和严重程度
                    issue["confidence"] = confirmed.get("confidence", 0.5)
                    issue["severity"] = confirmed.get("severity", "blocker")
                    if confirmed.get("location"):
                        issue["location"] = confirmed["location"]
                    if confirmed.get("additional_notes"):
                        issue["additional_notes"] = confirmed["additional_notes"]
                    confirmed_issues.append(issue)
                else:
                    # 二次确认明确否决，降级为次要问题
                    issue["severity"] = "minor"
                    issue["confidence"] = confirmed.get("confidence", 0.3)
                    confirmed_issues.append(issue)
            else:
                # 非Blocker级问题，直接保留
                issue["confidence"] = issue.get("confidence", 0.7)
                confirmed_issues.append(issue)
        
        # 更新分析结果
        analysis_result["issues"] = confirmed_issues
        
        # 重新计算总体评估
        analysis_result["overall_assessment"] = self._calculate_overall_assessment(confirmed_issues)
        
        return analysis_result
    
    def _confirm_single_issue(
        self,
        issue: Dict[str, Any],
        screenshot_base64: str,
        device_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        对单个问题进行二次确认
        
        Args:
            issue: 问题信息
            screenshot_base64: 截图的 base64 编码
            device_context: 设备上下文
        
        Returns:
            确认结果
        """
        prompt = self.VLM_BLOCKER_CONFIRM_PROMPT.format(
            issue_description=issue.get("description", ""),
            device_model=device_context.get("device_model", "unknown"),
            os_version=device_context.get("os_version", "unknown"),
            resolution=device_context.get("resolution", "unknown"),
            theme=device_context.get("theme", "light")
        )
        
        try:
            response = self._call_vlm(screenshot_base64, prompt)
            return response
        except Exception as e:
            logger.error(f"二次确认失败: {e}")
            return {
                "confirmed": False,
                "confidence": 0.3,
                "severity": "minor"
            }
    
    def generate_assertion_events(
        self,
        analysis_result: Dict[str, Any],
        step_id: str
    ) -> List[Dict[str, Any]]:
        """
        生成断言事件
        
        Args:
            analysis_result: 分析结果
            step_id: 步骤 ID
        
        Returns:
            断言事件列表
        """
        events = []
        has_issues = False
        
        issues = analysis_result.get("issues", [])
        for issue in issues:
            has_issues = True
            event = {
                "event_type": "assertion",
                "step_id": step_id,
                "dimension": issue.get("category", "unknown"),
                "assertion_type": issue.get("subtype", "unknown"),
                "status": "failed",
                "description": issue.get("description", ""),
                "severity": issue.get("severity", "medium"),
                "location": issue.get("location", ""),
                "suggestion": issue.get("suggestion", ""),
                "confidence": issue.get("confidence", 0.7),
            }
            events.append(event)
        
        # 如果没有问题，生成一个通过事件
        if not has_issues:
            events.append({
                "event_type": "assertion",
                "step_id": step_id,
                "dimension": "overall",
                "assertion_type": "ui_compatibility",
                "status": "passed",
                "description": "UI 兼容性检查通过",
                "severity": "info",
                "confidence": 1.0,
            })
        
        return events
    
    def calculate_overall_score(self, analysis_result: Dict[str, Any]) -> int:
        """
        计算总体分数
        
        Args:
            analysis_result: 分析结果
        
        Returns:
            总体分数 (0-100)
        """
        issues = analysis_result.get("issues", [])
        
        if not issues:
            return 100
        
        # 根据严重程度计算扣分
        severity_weights = {
            "blocker": 30,
            "major": 15,
            "minor": 5,
            "suggestion": 2
        }
        
        total_deduction = 0
        for issue in issues:
            severity = issue.get("severity", "minor")
            confidence = issue.get("confidence", 0.7)
            weight = severity_weights.get(severity, 5)
            total_deduction += weight * confidence
        
        score = max(0, 100 - total_deduction)
        return int(score)
    
    def _calculate_overall_assessment(self, issues: List[Dict]) -> str:
        """
        计算总体评估
        
        Args:
            issues: 问题列表
        
        Returns:
            overall_assessment: pass|warning|fail
        """
        has_blocker = any(i.get("severity") == "blocker" for i in issues)
        has_major = any(i.get("severity") == "major" for i in issues)
        
        if has_blocker:
            return "fail"
        elif has_major:
            return "warning"
        elif issues:
            return "warning"
        else:
            return "pass"
    
    def _build_prompt(
        self,
        page_name: str,
        dimensions: List[str],
        device_context: Dict[str, Any],
        page_description: str = "",
        target: str = "",
    ) -> str:
        """
        构建提示词（包含设备上下文）
        
        Args:
            page_name: 页面名称
            dimensions: 检测维度
            device_context: 设备上下文
        
        Returns:
            提示词字符串
        """
        # 构建维度说明
        dimension_descriptions = []
        for dim in dimensions:
            desc_map = {
                "layout": "- layout: 布局检查（元素重叠、遮挡、错位、溢出、间距问题）",
                "text": "- text: 文字检查（截断、乱码、显示不全、字体大小、对比度）",
                "image": "- image: 图片检查（拉伸、变形、加载失败、裁剪不当）",
                "adaptation": "- adaptation: 设备适配（异形屏遮挡、屏幕方向、大屏适配）",
                "theme": "- theme: 主题适配（深色模式对比度、不可见元素）",
                "page_state": "- page_state: 页面状态（白屏、加载中不消失、内容闪烁、空白内容、元素重复）"
            }
            dimension_descriptions.append(desc_map.get(dim, f"- {dim}: 检查"))
        
        dimensions_str = "\n".join(dimension_descriptions)
        extra_context = ""
        if target:
            extra_context += f"\n检查目标：{target}"
        if page_description:
            extra_context += f"\n检查标准：{page_description}"
        
        return self.VLM_UI_ANALYSIS_PROMPT.format(
            device_model=device_context.get("device_model", "unknown"),
            os_version=device_context.get("os_version", "unknown"),
            resolution=device_context.get("resolution", "unknown"),
            theme=device_context.get("theme", "light"),
            page_name=page_name,
            dimensions=dimensions_str + extra_context
        )
    
    def _call_vlm(self, image_base64: str, prompt: str) -> Dict[str, Any]:
        """
        调用 VLM API（带重试机制、缓存和增强的错误处理）
        
        Args:
            image_base64: 图片的 base64 编码
            prompt: 提示词
        
        Returns:
            VLM 响应结果
        """
        import time
        
        # 检查缓存（如果启用）
        cache_enabled = os.environ.get("VLM_CACHE_ENABLED", "true").lower() == "true"
        if cache_enabled:
            cache = get_global_vlm_cache()
            cached_result = cache.get(image_base64, prompt)
            if cached_result is not None:
                logger.debug("VLM 请求命中缓存")
                return cached_result
        
        # 构建消息
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_base64}"
                        }
                    }
                ]
            }
        ]
        
        # 调用 API（带重试）
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": self.model,
                        "messages": messages,
                        "max_tokens": 2000,
                        "stream": False,  # 明确禁用流式响应，确保返回标准 JSON
                    }
                )
                
                # 检查HTTP状态码
                if response.status_code >= 400:
                    last_error = f"HTTP错误 {response.status_code}: {response.text[:200]}"
                    logger.warning(f"VLM 请求失败 (尝试 {attempt + 1}/{self.max_retries + 1}): {last_error}")
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay * (2 ** attempt))
                        continue
                    return {
                        "error": last_error,
                        "status_code": response.status_code,
                        "raw_response": response.text[:500],
                    }
                
                # 解析响应
                try:
                    result = response.json()
                except json.JSONDecodeError as exc:
                    last_error = f"VLM HTTP 响应不是 JSON: {exc}"
                    logger.warning(f"VLM JSON解析失败 (尝试 {attempt + 1}/{self.max_retries + 1}): {last_error}")
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay * (2 ** attempt))
                        continue
                    return {
                        "error": last_error,
                        "status_code": response.status_code,
                        "raw_response": response.text[:500],
                    }
                
                # 验证响应结构
                if not result or "choices" not in result:
                    last_error = "VLM 响应缺少必要字段: choices"
                    logger.warning(f"VLM 响应结构无效 (尝试 {attempt + 1}/{self.max_retries + 1}): {last_error}")
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay * (2 ** attempt))
                        continue
                    return {
                        "error": last_error,
                        "status_code": response.status_code,
                        "raw_response": str(result)[:500],
                    }
                
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                
                if not content:
                    last_error = "VLM 返回空内容"
                    logger.warning(f"VLM 返回空内容 (尝试 {attempt + 1}/{self.max_retries + 1})")
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay * (2 ** attempt))
                        continue
                    return {
                        "error": last_error,
                        "status_code": response.status_code,
                        "raw_response": str(result)[:500],
                    }
                
                parsed_result = self._parse_vlm_response(content)
                
                # 如果没有错误，将结果存入缓存
                if cache_enabled and not parsed_result.get("error"):
                    cache.set(image_base64, prompt, parsed_result)
                
                return parsed_result
                
            except httpx.TimeoutException as exc:
                last_error = f"VLM 请求超时: {exc}"
                logger.warning(f"VLM 请求超时 (尝试 {attempt + 1}/{self.max_retries + 1}): {last_error}")
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * (2 ** attempt))
                    continue
                return {
                    "error": last_error,
                    "status_code": 408,
                    "raw_response": "",
                }
            except httpx.HTTPError as exc:
                last_error = f"VLM HTTP 错误: {exc}"
                logger.warning(f"VLM HTTP 错误 (尝试 {attempt + 1}/{self.max_retries + 1}): {last_error}")
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * (2 ** attempt))
                    continue
                return {
                    "error": last_error,
                    "status_code": getattr(exc.response, "status_code", None),
                    "raw_response": "",
                }
            except Exception as exc:
                last_error = f"VLM 请求异常: {exc}"
                logger.warning(f"VLM 请求异常 (尝试 {attempt + 1}/{self.max_retries + 1}): {last_error}")
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * (2 ** attempt))
                    continue
                return {
                    "error": last_error,
                    "status_code": None,
                    "raw_response": "",
                }
        
        return {
            "error": last_error or "VLM 请求失败",
            "status_code": None,
            "raw_response": "",
        }
    
    def _parse_vlm_response(self, response: str) -> Dict[str, Any]:
        """
        解析 VLM 响应
        
        Args:
            response: VLM 响应字符串
        
        Returns:
            解析后的结果字典
        """
        try:
            if not response or not response.strip():
                return {
                    "error": "VLM 返回空内容，无法解析 JSON",
                    "raw_response": response or "",
                }
            # 尝试提取 JSON
            from .event_parser import safe_json_parse
            parsed = safe_json_parse(response)
            if parsed:
                return parsed
            
            return {
                "error": "无法解析 JSON 响应",
                "raw_response": response[:500],
            }
        
        except Exception as e:
            logger.error(f"JSON 解析失败: {e}")
            return {
                "error": f"JSON 解析失败: {e}",
                "raw_response": response[:500],
            }
    
    def _get_default_result(self, error: str = None, page_name: str = "unknown") -> Dict[str, Any]:
        """
        获取默认结果（与方案格式一致）
        
        Args:
            error: 错误信息
            page_name: 页面名称
        
        Returns:
            默认结果字典
        """
        result = {
            "page_id": page_name,
            "overall_assessment": "warning",  # VLM分析失败时标记为警告，而非失败
            "confidence": 0.0,
            "issues": []
        }
        
        if error:
            result["error"] = error
        
        return result
    
    def close(self):
        """关闭客户端"""
        self.client.close()
