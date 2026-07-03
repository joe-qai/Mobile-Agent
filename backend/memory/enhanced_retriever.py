"""
增强检索器（Enhanced Retriever）
- 模糊匹配、同义词扩展、上下文加权
- 不依赖向量数据库，纯文本匹配
"""

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional


class EnhancedRetriever:
    """
    增强检索器：提供模糊匹配、同义词扩展、上下文加权
    
    特点：
    - 不依赖向量数据库
    - 纯文本匹配，轻量级
    - 支持同义词扩展
    - 支持上下文加权
    """
    
    # 同义词映射
    SYNONYM_MAP = {
        "点击": ["点击", "点按", "选择", "按下", "单击"],
        "输入": ["输入", "填写", "录入", "输入框", "键入"],
        "滑动": ["滑动", "滚动", "拖动", "划动"],
        "等待": ["等待", "等候", "暂停", "sleep"],
        "检查": ["检查", "验证", "校验", "确认", "断言"],
        "登录": ["登录", "登陆", "signin", "login"],
        "退出": ["退出", "登出", "注销", "signout", "logout"],
        "确认": ["确认", "确定", "ok", "yes", "同意"],
        "取消": ["取消", "关闭", "cancel", "no", "返回"],
        "搜索": ["搜索", "查找", "查询", "search"],
    }
    
    def __init__(self):
        """初始化增强检索器"""
        self._build_reverse_synonym_map()
    
    def _build_reverse_synonym_map(self):
        """构建反向同义词映射"""
        self._reverse_map = {}
        for main_word, synonyms in self.SYNONYM_MAP.items():
            for syn in synonyms:
                if syn not in self._reverse_map:
                    self._reverse_map[syn] = []
                self._reverse_map[syn].append(main_word)
    
    def fuzzy_search(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        threshold: float = 0.6,
        text_key: str = "text"
    ) -> List[Dict[str, Any]]:
        """
        模糊搜索
        
        Args:
            query: 查询文本
            candidates: 候选列表
            threshold: 相似度阈值
            text_key: 文本字段名
        
        Returns:
            匹配结果列表，按相似度降序排列
        """
        if not query or not candidates:
            return []
        
        results = []
        
        for candidate in candidates:
            text = candidate.get(text_key, "")
            if not text:
                continue
            
            # 计算相似度
            similarity = self.calculate_similarity(query, text)
            
            # 同义词扩展匹配
            expanded_similarity = self._synonym_similarity(query, text)
            similarity = max(similarity, expanded_similarity)
            
            if similarity >= threshold:
                result = candidate.copy()
                result["similarity"] = similarity
                results.append(result)
        
        # 按相似度降序排序
        results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
        return results
    
    def expand_synonyms(self, word: str) -> List[str]:
        """
        扩展同义词
        
        Args:
            word: 输入词
        
        Returns:
            同义词列表
        """
        synonyms = [word]
        
        # 从正向映射查找
        if word in self.SYNONYM_MAP:
            synonyms.extend(self.SYNONYM_MAP[word])
        
        # 从反向映射查找
        if word in self._reverse_map:
            for main_word in self._reverse_map[word]:
                if main_word not in synonyms:
                    synonyms.append(main_word)
                synonyms.extend(self.SYNONYM_MAP.get(main_word, []))
        
        return list(set(synonyms))
    
    def calculate_similarity(self, text1: str, text2: str) -> float:
        """
        计算文本相似度
        
        Args:
            text1: 文本1
            text2: 文本2
        
        Returns:
            相似度 (0-1)
        """
        if not text1 or not text2:
            return 0.0
        
        # 标准化
        text1 = text1.lower().strip()
        text2 = text2.lower().strip()
        
        # 完全匹配
        if text1 == text2:
            return 1.0
        
        # 包含关系
        if text1 in text2 or text2 in text1:
            shorter = min(len(text1), len(text2))
            longer = max(len(text1), len(text2))
            return shorter / longer
        
        # SequenceMatcher 相似度
        return SequenceMatcher(None, text1, text2).ratio()
    
    def context_weighted_search(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        context: Dict[str, Any],
        text_key: str = "text",
        context_key: str = "context"
    ) -> List[Dict[str, Any]]:
        """
        上下文加权搜索
        
        Args:
            query: 查询文本
            candidates: 候选列表
            context: 上下文信息
            text_key: 文本字段名
            context_key: 上下文字段名
        
        Returns:
            加权后的匹配结果
        """
        # 先进行模糊搜索
        results = self.fuzzy_search(query, candidates, threshold=0.5, text_key=text_key)
        
        # 应用上下文加权
        for result in results:
            candidate_context = result.get(context_key, {})
            context_boost = self._calculate_context_boost(context, candidate_context)
            result["similarity"] = min(1.0, result.get("similarity", 0) + context_boost)
        
        # 重新排序
        results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
        return results
    
    def _synonym_similarity(self, query: str, text: str) -> float:
        """
        基于同义词的相似度计算
        
        Args:
            query: 查询文本
            text: 目标文本
        
        Returns:
            相似度 (0-1)
        """
        # 提取关键词
        query_words = set(re.findall(r"[\u4e00-\u9fa5]+|[a-zA-Z]+", query.lower()))
        text_words = set(re.findall(r"[\u4e00-\u9fa5]+|[a-zA-Z]+", text.lower()))
        
        if not query_words or not text_words:
            return 0.0
        
        # 扩展同义词
        expanded_query = set()
        for word in query_words:
            expanded_query.update(self.expand_synonyms(word))
        
        expanded_text = set()
        for word in text_words:
            expanded_text.update(self.expand_synonyms(word))
        
        # 计算 Jaccard 相似度
        intersection = expanded_query & expanded_text
        union = expanded_query | expanded_text
        
        if not union:
            return 0.0
        
        return len(intersection) / len(union)
    
    def _calculate_context_boost(
        self,
        query_context: Dict[str, Any],
        candidate_context: Dict[str, Any]
    ) -> float:
        """
        计算上下文加权
        
        Args:
            query_context: 查询上下文
            candidate_context: 候选上下文
        
        Returns:
            加权值 (0-0.3)
        """
        if not query_context or not candidate_context:
            return 0.0
        
        boost = 0.0
        
        # 匹配的字段数量
        matched_fields = 0
        total_fields = 0
        
        for key, value in query_context.items():
            if key in candidate_context:
                total_fields += 1
                if candidate_context[key] == value:
                    matched_fields += 1
        
        if total_fields > 0:
            boost = (matched_fields / total_fields) * 0.3
        
        return boost
