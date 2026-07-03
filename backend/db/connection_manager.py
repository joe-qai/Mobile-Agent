"""
数据库连接管理器 - 统一管理 SQLite 连接，避免锁定问题

问题背景：
- database.py 每次操作都创建新连接
- persistent_memory.py 使用单例连接
- 多个连接同时访问导致 database is locked

解决方案：
1. 统一连接管理：所有数据库操作使用同一个连接池
2. 启用 WAL 模式：提高并发性能
3. 添加超时机制：避免长时间锁定
4. 连接上下文管理：自动提交和关闭
"""

import logging
import sqlite3
import threading
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)


class DatabaseConnectionManager:
    """
    数据库连接管理器（单例模式）
    
    特性：
    - 单例连接池：避免频繁创建/销毁连接
    - WAL 模式：提高并发读写性能
    - 超时机制：避免长时间锁定
    - 线程安全：使用线程锁保护连接
    - 上下文管理：自动提交和回滚
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, db_path: str = None):
        """单例模式"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, db_path: str = None):
        """初始化连接管理器"""
        if hasattr(self, '_initialized') and self._initialized:
            return
        
        if db_path is None:
            import os
            db_path = os.path.join(
                os.path.dirname(__file__), 
                "data", "app.db"
            )
        
        self.db_path = db_path
        self._connection: Optional[sqlite3.Connection] = None
        self._connection_lock = threading.Lock()
        self._transaction_lock = threading.Lock()
        self._initialized = True
        
        logger.info(f"数据库连接管理器初始化: {db_path}")
    
    def _create_connection(self) -> sqlite3.Connection:
        """创建新的数据库连接"""
        conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,  # 30 秒超时
            check_same_thread=False,  # 允许跨线程使用
            isolation_level=None  # 自动提交模式，手动控制事务
        )
        
        # 启用 WAL 模式（Write-Ahead Logging）
        # WAL 模式允许读写并发，提高性能
        conn.execute("PRAGMA journal_mode=WAL")
        
        # 设置其他优化参数
        conn.execute("PRAGMA synchronous=NORMAL")  # 平衡性能和安全
        conn.execute("PRAGMA cache_size=10000")  # 增加缓存
        conn.execute("PRAGMA temp_store=MEMORY")  # 临时表存储在内存
        
        conn.row_factory = sqlite3.Row
        
        logger.debug("创建新的数据库连接（WAL 模式）")
        return conn
    
    def get_connection(self) -> sqlite3.Connection:
        """获取数据库连接（自动检测并重新打开已关闭的连接）"""
        with self._connection_lock:
            if self._connection is None:
                self._connection = self._create_connection()
            else:
                # 检查连接是否仍然有效
                try:
                    # 尝试执行一个简单的查询来检测连接状态
                    self._connection.execute("SELECT 1")
                except sqlite3.ProgrammingError as e:
                    # 连接已关闭，重新创建
                    if "Cannot operate on a closed database" in str(e):
                        logger.debug("检测到已关闭的数据库连接，重新创建")
                        self._connection = self._create_connection()
                    else:
                        raise
            return self._connection
    
    def close_connection(self):
        """关闭数据库连接"""
        with self._connection_lock:
            if self._connection is not None:
                try:
                    self._connection.close()
                    self._connection = None
                    logger.debug("关闭数据库连接")
                except Exception as e:
                    logger.error(f"关闭数据库连接失败: {e}")
    
    @contextmanager
    def transaction(self):
        """
        事务上下文管理器

        使用示例：
        ```python
        with db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO ...")
            cursor.execute("UPDATE ...")
            # 自动提交，出错自动回滚
        ```
        """
        with self._transaction_lock:
            conn = self.get_connection()
            transaction_started = False
            try:
                # 开始事务
                conn.execute("BEGIN")
                transaction_started = True
                yield conn
                # 提交事务
                conn.execute("COMMIT")
            except Exception as e:
                # 回滚事务
                if transaction_started:
                    try:
                        conn.execute("ROLLBACK")
                    except sqlite3.Error as rollback_error:
                        logger.error(f"事务回滚失败: {rollback_error}")
                logger.error(f"事务执行失败，已回滚: {e}")
                raise
    
    @contextmanager
    def cursor(self):
        """
        游标上下文管理器
        
        使用示例：
        ```python
        with db_manager.cursor() as cursor:
            cursor.execute("SELECT * FROM ...")
            rows = cursor.fetchall()
        ```
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
        finally:
            cursor.close()
    
    def execute_query(self, query: str, params: tuple = ()):
        """
        执行查询并返回结果
        
        Args:
            query: SQL 查询语句
            params: 参数
        
        Returns:
            List[Dict]: 查询结果列表
        """
        with self.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def execute_update(self, query: str, params: tuple = ()):
        """
        执行更新并返回影响的行数
        
        Args:
            query: SQL 更新语句
            params: 参数
        
        Returns:
            int: 影响的行数或最后插入的 ID
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.lastrowid
    
    def execute_batch(self, queries: list):
        """
        批量执行多个 SQL 语句
        
        Args:
            queries: [(query, params), ...] 列表
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            for query, params in queries:
                cursor.execute(query, params)
    
    def vacuum(self):
        """清理数据库，优化空间"""
        with self.transaction() as conn:
            conn.execute("VACUUM")
            logger.info("数据库清理完成")


# 全局单例实例
db_manager = DatabaseConnectionManager()


# 便捷函数
def get_db_connection():
    """获取数据库连接"""
    return db_manager.get_connection()


def execute_query(query: str, params: tuple = ()):
    """执行查询"""
    return db_manager.execute_query(query, params)


def execute_update(query: str, params: tuple = ()):
    """执行更新"""
    return db_manager.execute_update(query, params)
