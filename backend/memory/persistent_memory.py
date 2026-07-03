"""
持久化记忆管理器（Persistent Memory Manager）
- 将 Agent 的操作经验保存到数据库
- 支持跨会话的经验检索和复用
- 实现自我进化机制
- 性能优化：统一连接管理、缓存、批量写入、异步处理
"""

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PersistentMemoryManager:
    """
    持久化记忆管理器（性能优化版）
    
    功能：
    1. 保存操作经验到数据库
    2. 查询历史经验
    3. 记录自我进化过程
    4. 提供经验检索接口
    
    性能优化：
    - 统一连接管理：使用全局连接管理器，避免连接冲突
    - 缓存机制：内存缓存常用查询结果
    - 批量写入：累积经验后批量写入，减少IO次数
    - 延迟写入：支持异步写入，不阻塞主流程
    """
    
    # 缓存配置
    _CACHE_SIZE = 1000  # 最大缓存条数
    _CACHE_TTL = 300    # 缓存过期时间（秒）
    
    def __init__(self, db_path: str = None, enable_cache: bool = True, batch_size: int = 10):
        """
        初始化持久化记忆管理器
        
        Args:
            db_path: 数据库路径，默认使用 backend/db/data/app.db
            enable_cache: 是否启用缓存
            batch_size: 批量写入的阈值
        """
        if db_path is None:
            import os
            db_path = os.path.join(
                os.path.dirname(__file__), 
                "..", "db", "data", "app.db"
            )
        self.db_path = db_path
        self.enable_cache = enable_cache
        self.batch_size = batch_size
        
        # 使用统一的连接管理器
        from backend.db.connection_manager import db_manager
        self.db_manager = db_manager
        
        # 缓存
        self._cache: Dict[str, Tuple[Any, float]] = {}  # key -> (value, timestamp)
        
        # 批量写入缓冲区
        self._pending_experiences: List[Dict[str, Any]] = []
        self._pending_evolutions: List[Dict[str, Any]] = []
        
        # 确保数据库已初始化
        self._ensure_db_initialized()
    
    def _ensure_db_initialized(self):
        """确保数据库已初始化"""
        try:
            from backend.db.database import init_db
            init_db()
        except Exception as e:
            logger.warning(f"数据库初始化失败: {e}")
    
    def _cache_get(self, key: str) -> Optional[Any]:
        """从缓存获取数据"""
        if not self.enable_cache:
            return None
        
        item = self._cache.get(key)
        if item:
            value, timestamp = item
            # 检查缓存是否过期
            if (datetime.now().timestamp() - timestamp) < self._CACHE_TTL:
                return value
            else:
                # 缓存过期，移除
                del self._cache[key]
        return None
    
    def _cache_set(self, key: str, value: Any):
        """设置缓存"""
        if not self.enable_cache:
            return
        
        # 如果缓存已满，移除最旧的条目
        if len(self._cache) >= self._CACHE_SIZE:
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]
        
        self._cache[key] = (value, datetime.now().timestamp())
    
    def _serialize_value(self, value: Any) -> str:
        """安全序列化值，处理不可序列化对象"""
        try:
            if hasattr(value, 'to_dict'):
                return json.dumps(value.to_dict(), ensure_ascii=False)
            if isinstance(value, (dict, list, str, int, float, bool, type(None))):
                return json.dumps(value, ensure_ascii=False)
            return json.dumps(str(value), ensure_ascii=False)
        except Exception:
            return json.dumps(str(value), ensure_ascii=False)

    def _flush_pending(self):
        """刷新待写入的数据"""
        try:
            if self._pending_experiences:
                self._batch_save_experiences(self._pending_experiences)
                self._pending_experiences = []
            
            if self._pending_evolutions:
                self._batch_log_evolutions(self._pending_evolutions)
                self._pending_evolutions = []
        except Exception as e:
            logger.error(f"批量写入失败: {e}")
    
    def _batch_save_experiences(self, experiences: List[Dict[str, Any]]):
        """批量保存经验"""
        try:
            with self.db_manager.transaction() as conn:
                cursor = conn.cursor()
                
                for exp in experiences:
                    # 检查是否已存在
                    cursor.execute(
                        "SELECT id, count FROM agent_action_memory WHERE experience_key = ?",
                        (exp["experience_key"],)
                    )
                    existing = cursor.fetchone()
                    
                    if existing:
                        new_count = existing["count"] + 1
                        cursor.execute(
                            "UPDATE agent_action_memory SET count = ?, updated_at = ?, result = ?, lesson = ?, context = ? WHERE experience_key = ?",
                            (
                                new_count,
                                datetime.now().isoformat(),
                                exp.get("result_json", "{}"),
                                exp.get("lesson"),
                                exp.get("context_json", "{}"),
                                exp["experience_key"]
                            )
                        )
                    else:
                        cursor.execute(
                            "INSERT INTO agent_action_memory "
                            "(experience_key, tool_name, arguments, result, experience_type, lesson, context, count) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                            (
                                exp["experience_key"],
                                exp["tool_name"],
                                exp.get("arguments_json", "{}"),
                                exp.get("result_json", "{}"),
                                exp["experience_type"],
                                exp.get("lesson"),
                                exp.get("context_json", "{}")
                            )
                        )
                
                logger.debug(f"批量保存了 {len(experiences)} 条经验")
        except Exception as e:
            logger.error(f"批量保存经验失败: {e}")
            raise
    
    def _batch_log_evolutions(self, evolutions: List[Dict[str, Any]]):
        """批量记录进化"""
        try:
            with self.db_manager.transaction() as conn:
                cursor = conn.cursor()
                
                for evo in evolutions:
                    cursor.execute(
                        "INSERT INTO agent_evolution_log "
                        "(task_id, evolution_type, description, before_state, after_state, impact_score) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            evo.get("task_id"),
                            evo["evolution_type"],
                            evo["description"],
                            evo.get("before_state_json", "{}"),
                            evo.get("after_state_json", "{}"),
                            evo.get("impact_score", 0.0)
                        )
                    )
                
                logger.debug(f"批量记录了 {len(evolutions)} 条进化")
        except Exception as e:
            logger.error(f"批量记录进化失败: {e}")
            raise
    
    def save_experience(
        self,
        experience_key: str,
        tool_name: str,
        arguments: Dict[str, Any],
        result: Any,
        experience_type: str,  # "success" or "failure"
        lesson: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        sync: bool = False  # 是否同步写入
    ) -> bool:
        """
        保存操作经验到数据库
        
        Args:
            experience_key: 经验唯一标识
            tool_name: 工具名称
            arguments: 工具参数
            result: 执行结果
            experience_type: 经验类型（success/failure）
            lesson: 失败教训
            context: 额外上下文
            sync: 是否同步写入（默认False，使用批量写入）
        
        Returns:
            bool: 是否成功
        """
        try:
            # 构建待写入的数据（使用安全序列化处理不可序列化对象）
            pending_item = {
                "experience_key": experience_key,
                "tool_name": tool_name,
                "arguments_json": self._serialize_value(arguments),
                "result_json": self._serialize_value(result),
                "experience_type": experience_type,
                "lesson": lesson,
                "context_json": self._serialize_value(context or {})
            }
            
            if sync:
                # 同步写入（立即执行）
                self._batch_save_experiences([pending_item])
            else:
                # 异步写入（添加到缓冲区）
                self._pending_experiences.append(pending_item)
                
                # 如果达到批量阈值，触发写入
                if len(self._pending_experiences) >= self.batch_size:
                    self._flush_pending()
            
            # 更新缓存
            cache_key = f"exp_{experience_key}"
            self._cache_set(cache_key, {
                "tool_name": tool_name,
                "arguments": arguments,
                "result": result,
                "experience_type": experience_type,
                "lesson": lesson,
                "context": context,
                "count": 1
            })
            
            return True
        except Exception as e:
            logger.error(f"保存经验失败: {e}")
            return False
    
    def query_experience(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        experience_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        查询历史经验
        
        Args:
            tool_name: 工具名称
            arguments: 工具参数
            experience_type: 经验类型过滤（可选）
        
        Returns:
            Dict: 经验信息，如果未找到返回None
        """
        # 构建查询键
        args_key = json.dumps(arguments, ensure_ascii=False)[:100]
        cache_key = f"exp_{tool_name}_{args_key}"
        
        # 先检查缓存
        cached = self._cache_get(cache_key)
        if cached:
            return cached
        
        try:
            # 构建查询
            query = "SELECT * FROM agent_action_memory WHERE experience_key LIKE ?"
            params = [f"{tool_name}_{args_key}%"]
            
            if experience_type:
                query += " AND experience_type = ?"
                params.append(experience_type)
            
            rows = self.db_manager.execute_query(query, tuple(params))
            
            if rows:
                row = rows[0]
                result = {
                    "id": row["id"],
                    "experience_key": row["experience_key"],
                    "tool_name": row["tool_name"],
                    "arguments": json.loads(row["arguments"]) if row["arguments"] else {},
                    "result": json.loads(row["result"]) if row["result"] else {},
                    "experience_type": row["experience_type"],
                    "lesson": row["lesson"],
                    "context": json.loads(row["context"]) if row["context"] else {},
                    "count": row["count"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"]
                }
                
                # 更新缓存
                self._cache_set(cache_key, result)
                return result
            
            return None
        except Exception as e:
            logger.error(f"查询经验失败: {e}")
            return None
    
    def query_similar_experiences(
        self,
        tool_name: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        查询相似经验（相同工具名称）
        
        Args:
            tool_name: 工具名称
            limit: 返回条数限制
        
        Returns:
            List[Dict]: 相似经验列表
        """
        try:
            rows = self.db_manager.execute_query(
                "SELECT * FROM agent_action_memory WHERE tool_name = ? ORDER BY count DESC LIMIT ?",
                (tool_name, limit)
            )
            
            results = []
            for row in rows:
                results.append({
                    "id": row["id"],
                    "experience_key": row["experience_key"],
                    "tool_name": row["tool_name"],
                    "arguments": json.loads(row["arguments"]) if row["arguments"] else {},
                    "result": json.loads(row["result"]) if row["result"] else {},
                    "experience_type": row["experience_type"],
                    "lesson": row["lesson"],
                    "context": json.loads(row["context"]) if row["context"] else {},
                    "count": row["count"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"]
                })
            
            return results
        except Exception as e:
            logger.error(f"查询相似经验失败: {e}")
            return []
    
    def log_evolution(
        self,
        evolution_type: str,
        description: str,
        task_id: Optional[str] = None,
        before_state: Optional[Dict[str, Any]] = None,
        after_state: Optional[Dict[str, Any]] = None,
        impact_score: float = 0.0,
        sync: bool = False
    ) -> bool:
        """
        记录自我进化过程
        
        Args:
            evolution_type: 进化类型
            description: 进化描述
            task_id: 任务ID（可选）
            before_state: 进化前状态
            after_state: 进化后状态
            impact_score: 影响评分（0-1）
            sync: 是否同步写入
        
        Returns:
            bool: 是否成功
        """
        try:
            pending_item = {
                "task_id": task_id,
                "evolution_type": evolution_type,
                "description": description,
                "before_state_json": json.dumps(before_state or {}, ensure_ascii=False),
                "after_state_json": json.dumps(after_state or {}, ensure_ascii=False),
                "impact_score": impact_score
            }
            
            if sync:
                self._batch_log_evolutions([pending_item])
            else:
                self._pending_evolutions.append(pending_item)
                if len(self._pending_evolutions) >= self.batch_size:
                    self._flush_pending()
            
            return True
        except Exception as e:
            logger.error(f"记录进化失败: {e}")
            return False
    
    def get_evolution_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        获取进化历史记录
        
        Args:
            limit: 返回条数限制
        
        Returns:
            List[Dict]: 进化历史列表
        """
        try:
            rows = self.db_manager.execute_query(
                "SELECT * FROM agent_evolution_log ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
            
            results = []
            for row in rows:
                results.append({
                    "id": row["id"],
                    "task_id": row["task_id"],
                    "evolution_type": row["evolution_type"],
                    "description": row["description"],
                    "before_state": json.loads(row["before_state"]) if row["before_state"] else {},
                    "after_state": json.loads(row["after_state"]) if row["after_state"] else {},
                    "impact_score": row["impact_score"],
                    "created_at": row["created_at"]
                })
            
            return results
        except Exception as e:
            logger.error(f"获取进化历史失败: {e}")
            return []
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        获取统计信息
        
        Returns:
            Dict: 统计信息
        """
        # 检查缓存
        cached = self._cache_get("stats")
        if cached:
            return cached
        
        try:
            # 总经验数
            rows = self.db_manager.execute_query("SELECT COUNT(*) as count FROM agent_action_memory")
            total_experiences = rows[0]["count"] if rows else 0
            
            # 成功经验数
            rows = self.db_manager.execute_query("SELECT COUNT(*) as count FROM agent_action_memory WHERE experience_type = 'success'")
            success_count = rows[0]["count"] if rows else 0
            
            # 失败经验数
            rows = self.db_manager.execute_query("SELECT COUNT(*) as count FROM agent_action_memory WHERE experience_type = 'failure'")
            failure_count = rows[0]["count"] if rows else 0
            
            # 进化记录数
            rows = self.db_manager.execute_query("SELECT COUNT(*) as count FROM agent_evolution_log")
            evolution_count = rows[0]["count"] if rows else 0
            
            # 最常用工具
            rows = self.db_manager.execute_query(
                "SELECT tool_name, SUM(count) as total FROM agent_action_memory "
                "GROUP BY tool_name ORDER BY total DESC LIMIT 5"
            )
            top_tools = []
            for row in rows:
                top_tools.append({
                    "tool_name": row["tool_name"],
                    "count": row["total"]
                })
            
            success_rate = success_count / total_experiences if total_experiences > 0 else 0.0
            
            stats = {
                "total_experiences": total_experiences,
                "success_count": success_count,
                "failure_count": failure_count,
                "success_rate": success_rate,
                "evolution_count": evolution_count,
                "top_tools": top_tools
            }
            
            # 更新缓存
            self._cache_set("stats", stats)
            return stats
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            return {
                "total_experiences": 0,
                "success_count": 0,
                "failure_count": 0,
                "success_rate": 0.0,
                "evolution_count": 0,
                "top_tools": []
            }
    
    def flush(self):
        """手动刷新所有待写入的数据"""
        self._flush_pending()
    
    def clear_cache(self):
        """清空缓存"""
        self._cache.clear()
    
    def close(self):
        """关闭资源"""
        self._flush_pending()
        self.clear_cache()
