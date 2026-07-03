"""
短期记忆（Short-term Memory）
- 生命周期：数天~数周，定期衰减
- 作用：近期执行经验，辅助当前任务决策
- 存储：SQLite（轻量、可查询、无需额外依赖）
- 核心：失败模式匹配 + 近期经验检索
"""

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional


class ShortTermMemory:
    """
    短期记忆：SQLite存储的近期执行经验
    
    用法：
        stm = ShortTermMemory("data/short_term.db")
        stm.record_task(task_log)
        stm.find_similar_failures("登录按钮点击失败")
        stm.get_recent_stats(days=7)
    """

    def __init__(self, db_path: str = "data/short_term.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        """初始化数据库表"""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS task_logs (
                task_id TEXT PRIMARY KEY,
                goal TEXT,
                steps_json TEXT,           -- 步骤列表的JSON
                success INTEGER,           -- 0/1
                total_steps INTEGER,
                success_count INTEGER,
                total_retries INTEGER,
                duration_ms REAL,
                created_at REAL,
                environment TEXT
            );

            CREATE TABLE IF NOT EXISTS failure_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                step_id TEXT,
                error_type TEXT,           -- 感知/思考/决策/行动
                error_description TEXT,
                root_cause TEXT,
                solution TEXT,
                occurred_at REAL,
                FOREIGN KEY (task_id) REFERENCES task_logs(task_id)
            );

            CREATE TABLE IF NOT EXISTS ui_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                screen_name TEXT,
                element_id TEXT,
                change_type TEXT,          -- 新增/消失/变化
                description TEXT,
                detected_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_failure_error_type 
                ON failure_patterns(error_type);
            CREATE INDEX IF NOT EXISTS idx_failure_description 
                ON failure_patterns(error_description);
            CREATE INDEX IF NOT EXISTS idx_task_logs_time 
                ON task_logs(created_at);
            CREATE INDEX IF NOT EXISTS idx_ui_changes_screen 
                ON ui_changes(screen_name);
        """)
        self.conn.commit()

    # ── 任务记录 ──

    def record_task(self, task_log: dict):
        """
        记录一次任务的完整日志
        
        Args:
            task_log: WorkingMemory.end_task() 返回的字典
        """
        summary = task_log.get("summary", {})
        self.conn.execute(
            """INSERT OR REPLACE INTO task_logs 
               (task_id, goal, steps_json, success, total_steps, 
                success_count, total_retries, duration_ms, created_at, environment)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_log["task_id"],
                task_log["goal"],
                json.dumps(task_log.get("steps", []), ensure_ascii=False),
                1 if summary.get("is_completed", False) else 0,
                summary.get("total_steps", 0),
                summary.get("success_count", 0),
                summary.get("total_retries", 0),
                summary.get("total_duration_ms", 0),
                time.time(),
                json.dumps(task_log.get("environment", {}), ensure_ascii=False),
            )
        )

        # 记录失败步骤为failure_patterns
        for step in task_log.get("steps", []):
            if not step.get("success", True):
                self.conn.execute(
                    """INSERT INTO failure_patterns 
                       (task_id, step_id, error_type, error_description, root_cause, solution, occurred_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        task_log["task_id"],
                        step.get("step_id", ""),
                        step.get("error_type", "未知"),  # 需要反思模块填充
                        step.get("observation", ""),
                        step.get("root_cause", ""),       # 需要反思模块填充
                        step.get("solution", ""),         # 需要反思模块填充
                        step.get("timestamp", time.time()),
                    )
                )

        self.conn.commit()

    def update_failure_analysis(self, task_id: str, step_id: str,
                                error_type: str, root_cause: str, solution: str):
        """
        反思模块分析后，更新失败记录的根因和解决方案
        
        Args:
            task_id, step_id: 定位具体失败步骤
            error_type: 感知/思考/决策/行动
            root_cause: 根因分析
            solution: 建议的解决方案
        """
        self.conn.execute(
            """UPDATE failure_patterns 
               SET error_type=?, root_cause=?, solution=? 
               WHERE task_id=? AND step_id=?""",
            (error_type, root_cause, solution, task_id, step_id)
        )
        self.conn.commit()

    # ── 查询接口 ──

    def find_similar_failures(self, error_description: str,
                              limit: int = 5) -> list[dict]:
        """
        查找历史类似失败案例
        
        Args:
            error_description: 当前遇到的错误描述
            limit: 返回数量
        Returns:
            匹配的失败记录列表
        """
        # 简单关键词匹配（后续可升级为语义检索）
        rows = self.conn.execute(
            """SELECT * FROM failure_patterns 
               WHERE error_description LIKE ? 
               ORDER BY occurred_at DESC LIMIT ?""",
            (f"%{error_description}%", limit)
        ).fetchall()

        return [dict(r) for r in rows]

    def find_failures_by_type(self, error_type: str,
                              limit: int = 10) -> list[dict]:
        """
        按错误类型查询（感知/思考/决策/行动）
        """
        rows = self.conn.execute(
            """SELECT * FROM failure_patterns 
               WHERE error_type=? 
               ORDER BY occurred_at DESC LIMIT ?""",
            (error_type, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_stats(self, days: int = 7) -> dict:
        """
        获取最近N天的执行统计
        
        Returns:
            {total_tasks, success_rate, avg_retries, common_failures}
        """
        cutoff = time.time() - days * 86400

        total = self.conn.execute(
            "SELECT COUNT(*) FROM task_logs WHERE created_at > ?", (cutoff,)
        ).fetchone()[0]

        successes = self.conn.execute(
            "SELECT COUNT(*) FROM task_logs WHERE created_at > ? AND success=1",
            (cutoff,)
        ).fetchone()[0]

        avg_retries = self.conn.execute(
            "SELECT AVG(total_retries) FROM task_logs WHERE created_at > ?",
            (cutoff,)
        ).fetchone()[0] or 0

        # 最常见失败类型
        common = self.conn.execute(
            """SELECT error_type, COUNT(*) as cnt 
               FROM failure_patterns WHERE occurred_at > ? 
               GROUP BY error_type ORDER BY cnt DESC LIMIT 5""",
            (cutoff,)
        ).fetchall()

        return {
            "total_tasks": total,
            "success_rate": successes / max(total, 1),
            "avg_retries": round(avg_retries, 2),
            "common_failure_types": [{"type": r["error_type"], "count": r["cnt"]} for r in common],
        }

    def get_recent_tasks(self, limit: int = 10) -> list[dict]:
        """获取最近N条任务记录"""
        rows = self.conn.execute(
            """SELECT task_id, goal, success, total_steps, duration_ms, created_at 
               FROM task_logs ORDER BY created_at DESC LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ── UI变化记录 ──

    def record_ui_change(self, screen_name: str, element_id: str,
                         change_type: str, description: str):
        """记录UI元素变化"""
        self.conn.execute(
            """INSERT INTO ui_changes 
               (screen_name, element_id, change_type, description, detected_at)
               VALUES (?, ?, ?, ?, ?)""",
            (screen_name, element_id, change_type, description, time.time())
        )
        self.conn.commit()

    def get_ui_changes(self, screen_name: str = None,
                       since: float = None) -> list[dict]:
        """查询UI变化"""
        query = "SELECT * FROM ui_changes"
        params = []
        conditions = []

        if screen_name:
            conditions.append("screen_name=?")
            params.append(screen_name)
        if since:
            conditions.append("detected_at>?")
            params.append(since)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY detected_at DESC"
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ── 衰减清理 ──

    def decay(self, max_age_days: int = 30):
        """
        清理过期记录（短期记忆衰减）
        
        Args:
            max_age_days: 保留最近多少天的记录
        """
        cutoff = time.time() - max_age_days * 86400
        self.conn.execute("DELETE FROM task_logs WHERE created_at < ?", (cutoff,))
        self.conn.execute("DELETE FROM failure_patterns WHERE occurred_at < ?", (cutoff,))
        self.conn.execute("DELETE FROM ui_changes WHERE detected_at < ?", (cutoff,))
        self.conn.commit()

    def close(self):
        """关闭数据库连接"""
        self.conn.close()
