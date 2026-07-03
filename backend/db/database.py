"""数据库模块 - 提供SQLite数据库操作"""

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.db import connection_manager as managed_db

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "app.db")


def _sync_managed_db_path():
    """Keep the shared connection manager aligned with tests that swap DB_PATH."""
    manager = managed_db.db_manager
    if manager.db_path != DB_PATH:
        manager.close_connection()
        manager.db_path = DB_PATH
        manager._connection = None  # 强制下次 get_connection() 创建新连接


def _close_managed_test_connection():
    """Keep production reuse while leaving pytest cleanup to the root fixture."""
    return


def _ensure_column(cursor, table, column, definition):
    """Add a column to a table if it does not exist (idempotent)."""
    cursor.execute(f"PRAGMA table_info({table})")
    columns = {row[1] for row in cursor.fetchall()}
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    """初始化数据库表结构"""
    _sync_managed_db_path()
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with managed_db.db_manager.transaction() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT UNIQUE,
                version TEXT DEFAULT '1.0.0',
                file_path TEXT,
                source TEXT DEFAULT 'local',
                project_id INTEGER,
                system_os TEXT DEFAULT 'Android',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                script_id INTEGER,
                apk_id INTEGER,
                task_type TEXT DEFAULT 'script',
                project_id INTEGER,
                parent_task_id INTEGER,
                task_role TEXT DEFAULT 'child',
                platform TEXT DEFAULT 'Android',
                device_id TEXT,
                remark TEXT,
                task_text TEXT,
                name TEXT,
                test_type TEXT DEFAULT 'normal',
                status TEXT DEFAULT 'pending',
                result TEXT,
                log TEXT,
                extra TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT,
                FOREIGN KEY (script_id) REFERENCES scripts(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT,
                description TEXT,
                category TEXT DEFAULT 'general',
                model_type TEXT DEFAULT 'llm',
                is_active INTEGER DEFAULT 1
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS apks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                package_name TEXT NOT NULL DEFAULT '',
                version TEXT DEFAULT '1.0.0',
                file_path TEXT NOT NULL,
                file_hash TEXT,
                remark TEXT,
                status TEXT DEFAULT 'uploading',
                file_size INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_task_id INTEGER,
                task_id INTEGER,
                event_type TEXT,
                dimension TEXT,
                name TEXT,
                status TEXT,
                target TEXT,
                message TEXT,
                severity TEXT,
                step_index INTEGER,
                evidence TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (parent_task_id) REFERENCES tasks(id),
                FOREIGN KEY (task_id) REFERENCES tasks(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_task_id INTEGER,
                task_id INTEGER,
                artifact_type TEXT,
                relative_path TEXT,
                step_index INTEGER,
                assertion_name TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (parent_task_id) REFERENCES tasks(id),
                FOREIGN KEY (task_id) REFERENCES tasks(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT UNIQUE NOT NULL,
                brand TEXT,
                model TEXT,
                os_version TEXT,
                platform TEXT DEFAULT '',
                resolution TEXT,
                density TEXT,
                last_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        _ensure_column(cursor, "devices", "status", "TEXT DEFAULT 'offline'")
        _ensure_column(cursor, "devices", "wifi_ip", "TEXT")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS compat_vlm_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                activity TEXT NOT NULL,
                step_name TEXT NOT NULL,
                dom_hash TEXT NOT NULL,
                vlm_result TEXT NOT NULL,
                screenshot_base64 TEXT,
                dimensions TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(device_id, activity, step_name, dom_hash)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS compat_audit_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cache_id INTEGER NOT NULL REFERENCES compat_vlm_cache(id),
                parent_task_id INTEGER NOT NULL,
                child_task_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                issue_type TEXT,
                issue_detail TEXT,
                remark TEXT,
                reviewed_by TEXT,
                reviewed_at TEXT,
                first_seen_task_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS compat_analysis_baselines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                device_id TEXT NOT NULL,
                activity TEXT NOT NULL DEFAULT '',
                step_name TEXT NOT NULL DEFAULT '',
                dom_hash TEXT NOT NULL DEFAULT '',
                screenshot_hash TEXT NOT NULL DEFAULT '',
                screenshot_body_hash TEXT NOT NULL DEFAULT '',
                vlm_result TEXT NOT NULL,
                screenshot_base64 TEXT,
                annotated_screenshot_base64 TEXT,
                review_status TEXT NOT NULL DEFAULT 'pending',
                remark TEXT,
                reviewed_by TEXT,
                reviewed_at TEXT,
                source_parent_task_id INTEGER NOT NULL,
                source_child_task_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(project_id, device_id, activity, step_name, dom_hash, screenshot_hash)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_action_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experience_key TEXT UNIQUE NOT NULL,
                tool_name TEXT NOT NULL,
                arguments TEXT,
                result TEXT,
                experience_type TEXT NOT NULL,
                lesson TEXT,
                context TEXT,
                count INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notification_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                conditions TEXT DEFAULT '{}',
                channels TEXT DEFAULT '["feishu"]',
                enabled BOOLEAN DEFAULT 1,
                priority INTEGER DEFAULT 10,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notification_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER,
                channel TEXT,
                event_type TEXT,
                task_name TEXT,
                status TEXT,
                sent_at TEXT DEFAULT CURRENT_TIMESTAMP,
                error_msg TEXT DEFAULT ''
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_evolution_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                evolution_type TEXT NOT NULL,
                description TEXT NOT NULL,
                before_state TEXT,
                after_state TEXT,
                impact_score REAL DEFAULT 0.0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_action_memory_tool ON agent_action_memory(tool_name)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_action_memory_type ON agent_action_memory(experience_type)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_evolution_log_created ON agent_evolution_log(created_at)"
        )

    _run_migrations()


def _run_migrations():
    """运行数据库迁移（使用 schema 版本表追踪，避免重复执行）"""
    _sync_managed_db_path()
    with managed_db.db_manager.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )

    # 检查并添加缺失的列
    with managed_db.db_manager.transaction() as conn:
        cursor = conn.cursor()

        # 检查 tasks 表结构并添加缺失的列
        cursor.execute("PRAGMA table_info(tasks)")
        columns = {row[1] for row in cursor.fetchall()}

        tasks_columns = {
            "apk_id": "INTEGER",
            "task_type": "TEXT DEFAULT 'script'",
            "project_id": "INTEGER",
            "parent_task_id": "INTEGER",
            "task_role": "TEXT DEFAULT 'child'",
            "platform": "TEXT DEFAULT 'Android'",
            "device_id": "TEXT",
            "task_text": "TEXT",
            "name": "TEXT",
            "test_type": "TEXT DEFAULT 'normal'",
            "result": "TEXT",
            "log": "TEXT",
            "extra": "TEXT",
            "updated_at": "TEXT",
            "completed_at": "TEXT",
        }

        for col_name, col_def in tasks_columns.items():
            if col_name not in columns:
                try:
                    # SQLite 不支持在 ALTER TABLE 中使用非常量默认值
                    # 先添加列，然后更新现有记录
                    if col_name == "updated_at":
                        cursor.execute(f"ALTER TABLE tasks ADD COLUMN {col_name} TEXT")
                        # 更新现有记录的 updated_at 为 created_at
                        cursor.execute(
                            "UPDATE tasks SET updated_at = created_at WHERE updated_at IS NULL"
                        )
                    else:
                        cursor.execute(
                            f"ALTER TABLE tasks ADD COLUMN {col_name} {col_def}"
                        )
                except sqlite3.OperationalError as e:
                    # 列可能已经存在，忽略错误
                    if "duplicate column name" not in str(e).lower():
                        raise

        # 检查 scripts 表是否有 updated_at 列
        cursor.execute("PRAGMA table_info(scripts)")
        columns = {row[1] for row in cursor.fetchall()}
        if "updated_at" not in columns:
            cursor.execute("ALTER TABLE scripts ADD COLUMN updated_at TEXT")
            cursor.execute(
                "UPDATE scripts SET updated_at = created_at WHERE updated_at IS NULL"
            )

        # 检查 projects 表是否有 updated_at 列
        cursor.execute("PRAGMA table_info(projects)")
        columns = {row[1] for row in cursor.fetchall()}
        if "updated_at" not in columns:
            cursor.execute("ALTER TABLE projects ADD COLUMN updated_at TEXT")
            cursor.execute(
                "UPDATE projects SET updated_at = created_at WHERE updated_at IS NULL"
            )

        _ensure_column(
            cursor, "compat_analysis_baselines", "annotated_screenshot_base64", "TEXT"
        )
        _ensure_column(
            cursor,
            "compat_analysis_baselines",
            "screenshot_body_hash",
            "TEXT DEFAULT ''",
        )

    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_task_events_parent_task_id ON task_events(parent_task_id)",
        "CREATE INDEX IF NOT EXISTS idx_task_events_task_id ON task_events(task_id)",
        "CREATE INDEX IF NOT EXISTS idx_task_events_event_type ON task_events(event_type)",
        "CREATE INDEX IF NOT EXISTS idx_task_events_parent_task_id_event_type ON task_events(parent_task_id, event_type)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_parent_task_id ON tasks(parent_task_id)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_device_id ON tasks(device_id)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
        "CREATE INDEX IF NOT EXISTS idx_task_artifacts_parent_task_id ON task_artifacts(parent_task_id)",
        "CREATE INDEX IF NOT EXISTS idx_task_artifacts_task_id ON task_artifacts(task_id)",
        "CREATE INDEX IF NOT EXISTS idx_task_artifacts_artifact_type ON task_artifacts(artifact_type)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_status_task_role_created_at ON tasks(status, task_role, created_at)",
    ]
    for sql in indexes:
        try:
            managed_db.db_manager.execute_update(sql)
        except sqlite3.OperationalError:
            pass


def execute_query(query: str, params: tuple = ()) -> List[Dict[str, Any]]:
    """执行查询并返回结果列表"""
    _sync_managed_db_path()
    try:
        return managed_db.execute_query(query, params)
    finally:
        _close_managed_test_connection()


def execute_update(query: str, params: tuple = ()) -> int:
    """执行更新并返回影响的行数"""
    _sync_managed_db_path()
    try:
        return managed_db.execute_update(query, params)
    finally:
        _close_managed_test_connection()


# 项目管理


def create_project(name: str, description: str = "") -> int:
    """创建项目"""
    query = """
        INSERT INTO projects (name, description)
        VALUES (?, ?)
    """
    return execute_update(query, (name, description))


def get_projects() -> List[Dict[str, Any]]:
    """获取所有项目"""
    query = "SELECT * FROM projects ORDER BY created_at DESC"
    return execute_query(query)


def get_project(project_id: int) -> Optional[Dict[str, Any]]:
    """获取项目详情"""
    query = "SELECT * FROM projects WHERE id = ?"
    result = execute_query(query, (project_id,))
    return result[0] if result else None


def update_project(project_id: int, name: str, description: str) -> bool:
    """更新项目"""
    query = """
        UPDATE projects SET name = ?, description = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """
    _sync_managed_db_path()
    return execute_update(query, (name, description, project_id)) > 0


def delete_project(project_id: int) -> bool:
    """删除项目"""
    query = "DELETE FROM projects WHERE id = ?"
    _sync_managed_db_path()
    try:
        with managed_db.db_manager.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (project_id,))
            return cursor.rowcount > 0
    finally:
        _close_managed_test_connection()


# 脚本管理


def _compute_content_hash(content: str) -> str:
    """计算内容哈希值"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def find_duplicate_script(content: str, system_os: str = None) -> Optional[int]:
    """查找重复内容的脚本，返回脚本ID或None

    Args:
        content: 脚本内容
        system_os: 目标系统（Android/iOS/HarmonyOS）。如果提供，需要同时匹配内容和系统才视为重复

    Returns:
        重复脚本的ID，如果不存在重复则返回None
    """
    content_hash = _compute_content_hash(content)

    if system_os:
        # 系统+内容都相同才算重复
        query = "SELECT id FROM scripts WHERE content_hash = ? AND system_os = ?"
        result = execute_query(query, (content_hash, system_os))
    else:
        # 只检查内容重复（保持向后兼容）
        query = "SELECT id FROM scripts WHERE content_hash = ?"
        result = execute_query(query, (content_hash,))

    if result:
        return result[0]["id"]
    return None


def find_script_by_name(name: str, project_id: int = None) -> Optional[int]:
    """查找同名脚本，返回脚本ID或None"""
    if project_id:
        query = "SELECT id FROM scripts WHERE name = ? AND project_id = ?"
        result = execute_query(query, (name, project_id))
    else:
        query = "SELECT id FROM scripts WHERE name = ?"
        result = execute_query(query, (name,))
    if result:
        return result[0]["id"]
    return None


def create_script(
    name: str,
    content: str,
    version: str = "1.0.0",
    file_path: str = "",
    source: str = "local",
    project_id: int = None,
    allow_duplicate: bool = False,
    system_os: str = "Android",
) -> dict:
    """
    创建脚本（带防重检查）

    Args:
        name: 脚本名称
        content: 脚本内容
        version: 版本号
        file_path: 文件路径
        source: 来源（local/agent）
        project_id: 项目ID
        allow_duplicate: 是否允许重复内容
        system_os: 目标系统（Android/iOS/HarmonyOS）

    Returns:
        {
            'success': bool,
            'script_id': int,
            'message': str,
            'is_duplicate': bool
        }
    """
    # 检查内容重复（需要同时匹配系统类型和内容）
    duplicate_id = find_duplicate_script(content, system_os)
    if duplicate_id and not allow_duplicate:
        return {
            "success": False,
            "script_id": duplicate_id,
            "message": f"检测到重复脚本，系统[{system_os}]下已存在相同内容的脚本(ID: {duplicate_id})",
            "is_duplicate": True,
            "duplicate_type": "content",
        }

    # 检查名称重复
    name_id = find_script_by_name(name, project_id)
    if name_id and not allow_duplicate:
        # 生成带序号的新名称
        base_name = name
        counter = 1
        while name_id:
            name = f"{base_name}_{counter}"
            counter += 1
            name_id = find_script_by_name(name, project_id)

    content_hash = _compute_content_hash(content)

    query = """
        INSERT INTO scripts (name, content, content_hash, version, file_path, source, project_id, system_os)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    try:
        script_id = execute_update(
            query,
            (
                name,
                content,
                content_hash,
                version,
                file_path,
                source,
                project_id,
                system_os,
            ),
        )
        return {
            "success": True,
            "script_id": script_id,
            "message": f"脚本创建成功，ID: {script_id}",
            "is_duplicate": False,
            "final_name": name,
        }
    except sqlite3.IntegrityError:
        # 并发场景下的重复插入保护
        duplicate_id = find_duplicate_script(content, system_os)
        if duplicate_id:
            return {
                "success": False,
                "script_id": duplicate_id,
                "message": f"脚本已存在，ID: {duplicate_id}",
                "is_duplicate": True,
                "duplicate_type": "content",
            }
        else:
            # IntegrityError 但没有找到重复，可能是其他约束冲突
            return {
                "success": False,
                "script_id": None,
                "message": "保存脚本失败，可能存在数据库约束冲突",
                "is_duplicate": False,
                "duplicate_type": None,
            }


def _build_scripts_filter(
    project_id: int = None, source: str = None, system_os: str = None
) -> tuple[str, list]:
    conditions = []
    params = []

    if project_id:
        conditions.append("project_id = ?")
        params.append(project_id)
    if source:
        conditions.append("source = ?")
        params.append(source)
    if system_os:
        conditions.append("system_os = ?")
        params.append(system_os)

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    return where_clause, params


def get_scripts(
    project_id: int = None, source: str = None, system_os: str = None
) -> List[Dict[str, Any]]:
    """获取脚本列表"""
    query = "SELECT * FROM scripts"
    where_clause, params = _build_scripts_filter(project_id, source, system_os)
    query += where_clause
    query += " ORDER BY created_at DESC"

    return execute_query(query, tuple(params))


def get_scripts_meta(
    project_id: int = None, source: str = None, system_os: str = None
) -> List[Dict[str, Any]]:
    """获取脚本元数据列表（排除 content 大字段）"""
    query = "SELECT id, name, content_hash, version, file_path, source, project_id, created_at, updated_at, system_os FROM scripts"
    where_clause, params = _build_scripts_filter(project_id, source, system_os)
    query += where_clause
    query += " ORDER BY created_at DESC"

    return execute_query(query, tuple(params))


def get_scripts_page(
    page: int = 1,
    size: int = 10,
    project_id: int = None,
    source: str = None,
    system_os: str = None,
) -> List[Dict[str, Any]]:
    """获取脚本分页列表（排除 content 大字段）"""
    safe_page = max(page, 1)
    safe_size = max(min(size, 200), 1)
    offset = (safe_page - 1) * safe_size
    query = "SELECT id, name, content_hash, version, file_path, source, project_id, created_at, updated_at, system_os FROM scripts"
    where_clause, params = _build_scripts_filter(project_id, source, system_os)
    query += where_clause
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([safe_size, offset])
    return execute_query(query, tuple(params))


def count_scripts(
    project_id: int = None, source: str = None, system_os: str = None
) -> int:
    """统计脚本数量"""
    query = "SELECT COUNT(*) AS total FROM scripts"
    where_clause, params = _build_scripts_filter(project_id, source, system_os)
    result = execute_query(query + where_clause, tuple(params))
    return int(result[0]["total"]) if result else 0


def get_script(script_id: int) -> Optional[Dict[str, Any]]:
    """获取脚本详情"""
    query = "SELECT * FROM scripts WHERE id = ?"
    result = execute_query(query, (script_id,))
    return result[0] if result else None


def update_script(
    script_id: int,
    name: str,
    content: str,
    version: str,
    project_id: int = None,
    system_os: str = None,
) -> bool:
    """更新脚本"""
    query = """
        UPDATE scripts SET name = ?, content = ?, version = ?, updated_at = CURRENT_TIMESTAMP
    """
    params = [name, content, version]

    if project_id is not None:
        query += ", project_id = ?"
        params.append(project_id)

    if system_os is not None:
        query += ", system_os = ?"
        params.append(system_os)

    query += " WHERE id = ?"
    params.append(script_id)

    execute_update(query, tuple(params))
    return True


def delete_script(script_id: int) -> bool:
    """删除脚本"""
    query = "DELETE FROM scripts WHERE id = ?"
    _sync_managed_db_path()
    return execute_update(query, (script_id,)) > 0


def batch_delete_scripts(script_ids: List[int]) -> int:
    """批量删除脚本"""
    placeholders = ",".join(["?" for _ in script_ids])
    query = f"DELETE FROM scripts WHERE id IN ({placeholders})"
    _sync_managed_db_path()
    with managed_db.db_manager.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(query, tuple(script_ids))
        return cursor.rowcount


# 任务管理


def create_task(
    script_id: int,
    remark: str,
    device_ids: list = None,
    task_type: str = "script",
    project_id: int = None,
    name: str = None,
    test_type: str = "normal",
) -> int:
    """创建任务（支持多设备）"""
    # 将设备列表转换为逗号分隔的字符串
    device_ids_str = (
        ",".join(device_ids) if device_ids and isinstance(device_ids, list) else None
    )

    # 如果没有传 name，自动拼接 项目名/脚本名/系统类型
    if not name:
        script = get_script(script_id)
        script_name = script["name"] if script else "未知脚本"
        system_os = script.get("system_os", "Unknown") if script else "Unknown"
        project_name = ""
        if project_id:
            proj = get_project(project_id)
            project_name = proj["name"] if proj else ""
        task_name = (
            f"{project_name}/{script_name}/{system_os}"
            if project_name
            else f"{script_name}/{system_os}"
        )
    else:
        task_name = name

    query = """
        INSERT INTO tasks (script_id, device_id, remark, task_text, task_type, project_id, name, test_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    return execute_update(
        query,
        (
            script_id,
            device_ids_str,
            remark,
            task_name,
            task_type,
            project_id,
            task_name,
            test_type,
        ),
    )


UNKNOWN_DEVICE_DISPLAY_NAME = "未知设备"


def format_device_display_name(brand: str = None, model: str = None) -> str:
    """Format a user-facing device name from inventory brand/model fields."""
    parts = [
        str(value).strip() for value in (brand, model) if value and str(value).strip()
    ]
    return " ".join(parts) if parts else UNKNOWN_DEVICE_DISPLAY_NAME


def get_device_display_name_from_db(device_id: str) -> str:
    """从数据库 devices 表查询设备展示名（brand model (platform os_version)）。

    用于实时 mcp_tools 查询失败时的回退，避免展示设备序列号。
    未找到或字段缺失时返回空字符串，由调用方决定回退到 UNKNOWN_DEVICE_DISPLAY_NAME。
    """
    if not device_id:
        return ""
    row = get_device_info(device_id)
    if not row:
        return ""
    brand = (row.get("brand") or "").strip()
    model = (row.get("model") or "").strip()
    os_version = (row.get("os_version") or "").strip()
    parts = [p for p in (brand, model) if p]
    if not parts:
        return ""
    name = " ".join(parts)
    if os_version:
        platform = (row.get("platform") or "android").lower()
        name += f" ({platform} {os_version})"
    return name


def _split_device_ids(device_id: str) -> list[str]:
    if not device_id:
        return []
    return [part.strip() for part in str(device_id).split(",") if part.strip()]


def _get_device_display_map(device_ids: list[str]) -> dict[str, str]:
    unique_ids = sorted({device_id for device_id in device_ids if device_id})
    if not unique_ids:
        return {}

    placeholders = ",".join("?" for _ in unique_ids)
    rows = execute_query(
        f"SELECT device_id, brand, model FROM devices WHERE device_id IN ({placeholders})",
        tuple(unique_ids),
    )
    return {
        row["device_id"]: format_device_display_name(row.get("brand"), row.get("model"))
        for row in rows
    }


def _display_name_for_device_field(device_id: str, display_map: dict[str, str]) -> str:
    ids = _split_device_ids(device_id)
    if not ids:
        return ""
    return ", ".join(display_map.get(item, UNKNOWN_DEVICE_DISPLAY_NAME) for item in ids)


def _attach_device_display_names(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    device_ids = []
    for row in rows:
        device_ids.extend(_split_device_ids(row.get("device_id", "")))
    display_map = _get_device_display_map(device_ids)
    for row in rows:
        row["device_display_name"] = _display_name_for_device_field(
            row.get("device_id", ""), display_map
        )
    return rows


def get_tasks(
    project_id: int = None, status: str = None, limit: int = None, offset: int = 0
) -> List[Dict[str, Any]]:
    """获取任务列表（联查项目名称和脚本名称）"""
    query = """
        SELECT t.*, p.name as project_name, s.name as script_name
        FROM tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        LEFT JOIN scripts s ON t.script_id = s.id
        WHERE 1=1
    """
    params = []

    if project_id:
        query += " AND t.project_id = ?"
        params.append(project_id)
    if status:
        query += " AND t.status = ?"
        params.append(status)

    if not status:
        query += " AND t.status != 'deleted'"

    query += " ORDER BY t.created_at DESC"
    if limit is not None:
        safe_limit = max(min(int(limit), 500), 1)
        safe_offset = max(int(offset or 0), 0)
        query += " LIMIT ? OFFSET ?"
        params.extend([safe_limit, safe_offset])
    return _attach_device_display_names(execute_query(query, tuple(params)))


def get_all_tasks(limit: int = None, offset: int = 0) -> List[Dict[str, Any]]:
    """获取所有任务（包括已删除的，用于报告管理）"""
    query = """
        SELECT t.*, p.name as project_name, s.name as script_name
        FROM tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        LEFT JOIN scripts s ON t.script_id = s.id
    """
    query += " ORDER BY t.created_at DESC"
    params = []
    if limit is not None:
        safe_limit = max(min(int(limit), 500), 1)
        safe_offset = max(int(offset or 0), 0)
        query += " LIMIT ? OFFSET ?"
        params.extend([safe_limit, safe_offset])
    return _attach_device_display_names(execute_query(query, tuple(params)))


def get_reports_page(
    page: int = 1,
    size: int = 10,
    status: str = None,
    project_id: int = None,
    date_from: str = None,
    date_to: str = None,
) -> tuple:
    """获取报告分页列表（只含已完成/失败/部分失败的任务，排除子任务）"""
    safe_page = max(page, 1)
    safe_size = max(min(size, 200), 1)
    offset = (safe_page - 1) * safe_size

    base_query = """
        FROM tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        LEFT JOIN scripts s ON t.script_id = s.id
        WHERE t.status IN ('finished', 'failed', 'partial_failed')
          AND (t.task_role = 'parent' OR (t.task_role = 'child' AND t.parent_task_id IS NULL))
    """
    count_query = "SELECT COUNT(*) AS total" + base_query
    data_query = (
        """
        SELECT t.*, p.name as project_name, s.name as script_name
    """
        + base_query
    )

    params = []
    if status:
        data_query += " AND t.status = ?"
        count_query += " AND t.status = ?"
        params.append(status)
    if project_id is not None:
        data_query += " AND t.project_id = ?"
        count_query += " AND t.project_id = ?"
        params.append(project_id)
    if date_from:
        data_query += " AND t.completed_at >= ?"
        count_query += " AND t.completed_at >= ?"
        params.append(date_from)
    if date_to:
        data_query += " AND t.completed_at <= ?"
        count_query += " AND t.completed_at <= ?"
        params.append(date_to)

    data_query += " ORDER BY t.completed_at DESC LIMIT ? OFFSET ?"

    total = execute_query(count_query, tuple(params))
    total_count = int(total[0]["total"]) if total else 0

    items = _attach_device_display_names(
        execute_query(data_query, tuple(params + [safe_size, offset]))
    )
    return items, total_count


def get_compat_severity_counts() -> Dict[str, int]:
    """从所有 compat_analysis_baselines 行聚合四级严重度计数。

    遍历每个 vlm_result 字段中的 issues[].severity，使用映射表归一化：
      blocker -> blocker, major -> major, minor -> minor, suggestion -> suggestion
      critical -> blocker, info -> suggestion, warning -> minor
      pass -> suggestion, passed -> suggestion
      fail -> blocker, failed -> blocker, warn -> minor
    未知值默认归为 suggestion。

    如果 vlm_result 没有 issues 但有 overall_assessment，使用回退映射：
      fail/failed -> blocker, warning/warn -> minor, pass/passed -> suggestion
    """
    _sync_managed_db_path()

    from backend.compatibility.event_parser import safe_json_parse

    SEVERITY_MAP = {
        "blocker": "blocker",
        "major": "major",
        "minor": "minor",
        "suggestion": "suggestion",
        # Backwards compatibility mappings
        "critical": "blocker",
        "info": "suggestion",
        "warning": "minor",
        "pass": "suggestion",
        "passed": "suggestion",
        "fail": "blocker",
        "failed": "blocker",
        "warn": "minor",
    }

    OVERALL_ASSESSMENT_MAP = {
        "fail": "blocker",
        "failed": "blocker",
        "warning": "minor",
        "warn": "minor",
        "pass": "suggestion",
        "passed": "suggestion",
    }

    counts = {"blocker": 0, "major": 0, "minor": 0, "suggestion": 0}

    rows = execute_query("SELECT vlm_result FROM compat_analysis_baselines")
    for row in rows:
        vlm_result_str = row.get("vlm_result", "")
        if not vlm_result_str or not isinstance(vlm_result_str, str):
            continue
        try:
            data = safe_json_parse(vlm_result_str)
            if not data or not isinstance(data, dict):
                continue

            issues = data.get("issues", [])
            if issues and isinstance(issues, list):
                for issue in issues:
                    if not isinstance(issue, dict):
                        continue
                    severity = issue.get("severity", "")
                    if not severity:
                        continue
                    normalized = SEVERITY_MAP.get(str(severity).lower(), "suggestion")
                    counts[normalized] += 1
            else:
                # No issues -- fallback to overall_assessment
                overall = data.get("overall_assessment", "")
                if overall:
                    normalized = OVERALL_ASSESSMENT_MAP.get(
                        str(overall).lower(), "suggestion"
                    )
                    counts[normalized] += 1
        except Exception:
            # Skip unparseable rows gracefully
            continue

    return counts


def get_reports_stats() -> Dict[str, int]:
    """获取报告统计数据（总数 + blocker/major/minor/suggestion 严重度计数）。

    total 包含所有已完成任务（含普通测试报告）。
    blocker/major/minor/suggestion 仅从 UI 兼容分析基线聚合，
    普通测试报告不参与严重度统计。
    """
    _sync_managed_db_path()

    total = execute_query("""
        SELECT COUNT(*) AS count
        FROM tasks
        WHERE status IN ('finished', 'failed', 'partial_failed')
          AND (task_role = 'parent' OR (task_role = 'child' AND parent_task_id IS NULL))
    """)
    total_count = int(total[0]["count"]) if total else 0

    severity_counts = get_compat_severity_counts()

    return {
        "total": total_count,
        "blocker": severity_counts["blocker"],
        "major": severity_counts["major"],
        "minor": severity_counts["minor"],
        "suggestion": severity_counts["suggestion"],
    }


def get_task(task_id: int) -> Optional[Dict[str, Any]]:
    """获取任务详情（联查项目名称和脚本名称）"""
    query = """
        SELECT t.*, p.name as project_name, s.name as script_name
        FROM tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        LEFT JOIN scripts s ON t.script_id = s.id
        WHERE t.id = ?
    """
    result = _attach_device_display_names(execute_query(query, (task_id,)))
    return result[0] if result else None


def update_task(task_id: int, **kwargs) -> bool:
    """更新任务"""
    kwargs["updated_at"] = datetime.now(timezone.utc).isoformat()

    terminal_statuses = {"finished", "failed", "cancelled"}
    if "completed_at" not in kwargs and kwargs.get("status") in terminal_statuses:
        kwargs["completed_at"] = datetime.now(timezone.utc).isoformat()

    keys = kwargs.keys()
    query = f"""
        UPDATE tasks SET {", ".join([f"{k} = ?" for k in keys])}
        WHERE id = ?
    """
    params = list(kwargs.values()) + [task_id]
    _sync_managed_db_path()
    return execute_update(query, tuple(params)) > 0


def delete_task(task_id: int) -> bool:
    """删除任务 — 有报告的任务软删除（保留报告可访问），无报告的任务物理删除"""
    task = get_task(task_id)
    if not task:
        return False

    has_report = task.get("status") in ("finished", "failed") and task.get("result")
    if has_report:
        query = "UPDATE tasks SET status = 'deleted' WHERE id = ?"
    else:
        query = "DELETE FROM tasks WHERE id = ?"

    _sync_managed_db_path()
    return execute_update(query, (task_id,)) > 0


def batch_delete_tasks(task_ids: List[int]) -> int:
    """批量删除任务 — 有报告的任务软删除，无报告的任务物理删除"""
    deletable_ids = []
    soft_delete_ids = []
    for task_id in task_ids:
        task = get_task(task_id)
        if not task or task.get("status") == "running":
            continue
        has_report = task.get("status") in ("finished", "failed") and task.get("result")
        if has_report:
            soft_delete_ids.append(task_id)
        else:
            deletable_ids.append(task_id)

    row_count = 0
    _sync_managed_db_path()
    with managed_db.db_manager.transaction() as conn:
        cursor = conn.cursor()

        if soft_delete_ids:
            placeholders = ",".join(["?" for _ in soft_delete_ids])
            cursor.execute(
                f"UPDATE tasks SET status = 'deleted' WHERE id IN ({placeholders})",
                tuple(soft_delete_ids),
            )
            row_count += cursor.rowcount

        if deletable_ids:
            placeholders = ",".join(["?" for _ in deletable_ids])
            cursor.execute(
                f"DELETE FROM tasks WHERE id IN ({placeholders})", tuple(deletable_ids)
            )
            row_count += cursor.rowcount

    return row_count


def batch_delete_reports(task_ids: List[int]) -> int:
    """批量删除报告（只删除已完成的报告，不影响未完成的任务）

    同步删除本地文件：data/artifacts/compat/{parent_task_id}/ 目录及 task_artifacts、task_events 表记录。
    """
    if not task_ids:
        return 0

    # 1. 先删除本地文件（独立于数据库事务，失败不阻塞）
    try:
        import shutil

        from backend.compatibility.artifact_store import artifact_store

        for task_id in task_ids:
            dir_path = os.path.join(artifact_store.base_path, str(task_id))
            if os.path.exists(dir_path):
                try:
                    shutil.rmtree(dir_path)
                except Exception as e:
                    # 文件删除失败不阻塞数据库清理
                    import logging

                    logging.getLogger(__name__).warning(
                        "删除报告目录失败 %s: %s", dir_path, e
                    )
    except Exception:
        pass

    placeholders = ",".join(["?" for _ in task_ids])

    _sync_managed_db_path()
    with managed_db.db_manager.transaction() as conn:
        cursor = conn.cursor()

        # 2. 删除 task_artifacts 表关联记录
        cursor.execute(
            f"DELETE FROM task_artifacts WHERE parent_task_id IN ({placeholders})",
            tuple(task_ids),
        )

        # 3. 删除 task_events 表关联记录
        cursor.execute(
            f"DELETE FROM task_events WHERE parent_task_id IN ({placeholders})",
            tuple(task_ids),
        )

        # 4. 删除父任务的子任务（如果是兼容性测试父任务）
        cursor.execute(
            f"""
            DELETE FROM tasks
            WHERE parent_task_id IN ({placeholders})
              AND task_role = 'child'
        """,
            tuple(task_ids),
        )

        # 5. 删除已完成的父任务
        cursor.execute(
            f"""
            DELETE FROM tasks
            WHERE id IN ({placeholders})
              AND status IN ('finished', 'failed', 'partial_failed')
        """,
            tuple(task_ids),
        )
        parent_deleted = cursor.rowcount

        return parent_deleted


def delete_child_task(parent_task_id: int, device_id: str) -> bool:
    """删除兼容性测试中单个设备的子任务

    同步删除本地子目录：data/artifacts/compat/{parent_task_id}/{child_task_id}/ 及 task_artifacts 表记录。
    """
    # 1. 先查询 child_task_id
    _sync_managed_db_path()
    child_query = """
        SELECT id FROM tasks
        WHERE parent_task_id = ? AND device_id = ? AND task_role = 'child'
    """
    children = execute_query(child_query, (parent_task_id, device_id))
    if not children:
        return False
    child_task_id = children[0]["id"]

    # 2. 删除本地子目录（独立于数据库事务，失败不阻塞）
    try:
        import shutil

        from backend.compatibility.artifact_store import artifact_store

        dir_path = os.path.join(
            artifact_store.base_path, str(parent_task_id), str(child_task_id)
        )
        if os.path.exists(dir_path):
            try:
                shutil.rmtree(dir_path)
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(
                    "删除子任务目录失败 %s: %s", dir_path, e
                )
    except Exception:
        pass

    # 3. 删除数据库记录
    with managed_db.db_manager.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM task_artifacts WHERE task_id = ?", (child_task_id,))
        cursor.execute("DELETE FROM task_events WHERE task_id = ?", (child_task_id,))
        cursor.execute(
            """
            DELETE FROM tasks
            WHERE id = ?
        """,
            (child_task_id,),
        )
        return cursor.rowcount > 0


def get_child_tasks_by_parent(parent_task_id: int) -> List[Dict[str, Any]]:
    """获取父任务的所有子任务"""
    query = """
        SELECT * FROM tasks
        WHERE parent_task_id = ? AND task_role = 'child'
        ORDER BY created_at ASC
    """
    return _attach_device_display_names(execute_query(query, (parent_task_id,)))


# 配置管理


def set_config(
    key: str, value: str, description: str = "", category: str = "general"
) -> int:
    """设置配置"""
    _sync_managed_db_path()
    row_id = None
    with managed_db.db_manager.transaction() as conn:
        cursor = conn.cursor()
        # Try UPDATE first
        cursor.execute(
            "UPDATE configs SET value = ?, description = ?, category = ? WHERE key = ?",
            (value, description, category, key),
        )
        if cursor.rowcount == 0:
            # Key doesn't exist, INSERT instead
            cursor.execute(
                "INSERT INTO configs (key, value, description, category) VALUES (?, ?, ?, ?)",
                (key, value, description, category),
            )
        row_id = cursor.lastrowid

    invalidate_config_cache()
    return row_id


# 配置缓存
_config_cache = {}
_config_cache_time = 0
_config_cache_ttl = 60  # 缓存有效期60秒
_config_cache_lock = threading.Lock()


def get_config(key: str, default: str = None) -> str:
    """获取配置（带缓存）"""
    global _config_cache, _config_cache_time

    with _config_cache_lock:
        # 检查缓存是否有效
        now = _get_current_time()
        if now - _config_cache_time > _config_cache_ttl:
            _config_cache.clear()
            _config_cache_time = now

        if key in _config_cache:
            return _config_cache[key]

    query = "SELECT value FROM configs WHERE key = ?"
    result = execute_query(query, (key,))
    value = result[0]["value"] if result else default
    with _config_cache_lock:
        _config_cache[key] = value
    return value


def _get_current_time():
    """获取当前时间戳"""
    import time

    return int(time.time())


def invalidate_config_cache():
    """使配置缓存失效"""
    global _config_cache, _config_cache_time
    with _config_cache_lock:
        _config_cache.clear()
        _config_cache_time = 0


def get_configs_dict(
    keys: List[str], defaults: Dict[str, Any] = None
) -> Dict[str, Any]:
    """批量获取多个配置"""
    global _config_cache, _config_cache_time

    if not keys:
        return {}

    defaults = defaults or {}
    result = {}

    with _config_cache_lock:
        # 先检查缓存
        now = _get_current_time()
        if now - _config_cache_time > _config_cache_ttl:
            _config_cache.clear()
            _config_cache_time = now

        # 找出需要查询的key
        need_query = [k for k in keys if k not in _config_cache]

    if need_query:
        # 批量查询
        placeholders = ",".join("?" * len(need_query))
        query = f"SELECT key, value FROM configs WHERE key IN ({placeholders})"
        rows = execute_query(query, tuple(need_query))

        with _config_cache_lock:
            for row in rows:
                _config_cache[row["key"]] = row["value"]

    # 返回结果
    with _config_cache_lock:
        for key in keys:
            result[key] = _config_cache.get(key, defaults.get(key))

    return result


def get_configs(category: str = None) -> List[Dict[str, Any]]:
    """获取所有配置"""
    if category:
        query = "SELECT * FROM configs WHERE category = ?"
        return execute_query(query, (category,))
    query = "SELECT * FROM configs"
    return execute_query(query)


def delete_config(key: str) -> bool:
    """删除配置"""
    query = "DELETE FROM configs WHERE key = ?"
    _sync_managed_db_path()
    return execute_update(query, (key,)) > 0


# 通知规则管理


def create_notification_rule(
    name: str,
    event_type: str,
    conditions: str = "{}",
    channels: str = '["feishu"]',
    enabled: bool = True,
    priority: int = 10,
) -> int:
    """创建通知规则"""
    _sync_managed_db_path()
    enabled_str = "true" if enabled else "false"
    row_id = managed_db.execute_update(
        "INSERT INTO notification_rules (name, event_type, conditions, channels, enabled, priority) VALUES (?, ?, ?, ?, ?, ?)",
        (name, event_type, conditions, channels, enabled_str, priority),
    )
    return row_id


def get_notification_rules() -> List[Dict[str, Any]]:
    """获取所有通知规则（按优先级排序）"""
    return execute_query("SELECT * FROM notification_rules ORDER BY priority ASC")


def get_enabled_rules() -> List[Dict[str, Any]]:
    """获取启用的通知规则（按优先级排序）"""
    return execute_query(
        "SELECT * FROM notification_rules WHERE enabled = 'true' ORDER BY priority ASC"
    )


def update_notification_rule(
    rule_id: int,
    name: str = None,
    event_type: str = None,
    conditions: str = None,
    channels: str = None,
    enabled: bool = None,
    priority: int = None,
) -> bool:
    """更新通知规则"""
    _sync_managed_db_path()
    updates, params = [], []
    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if event_type is not None:
        updates.append("event_type = ?")
        params.append(event_type)
    if conditions is not None:
        updates.append("conditions = ?")
        params.append(conditions)
    if channels is not None:
        updates.append("channels = ?")
        params.append(channels)
    if enabled is not None:
        updates.append("enabled = ?")
        params.append("true" if enabled else "false")
    if priority is not None:
        updates.append("priority = ?")
        params.append(priority)
    if not updates:
        return False
    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(rule_id)
    managed_db.execute_update(
        f"UPDATE notification_rules SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )
    return True


def delete_notification_rule(rule_id: int) -> bool:
    """删除通知规则"""
    _sync_managed_db_path()
    managed_db.execute_update("DELETE FROM notification_rules WHERE id = ?", (rule_id,))
    return True


# 通知日志


def log_notification(
    rule_id: int,
    channel: str,
    event_type: str,
    task_name: str,
    status: str,
    error_msg: str = "",
) -> int:
    """记录通知发送日志"""
    _sync_managed_db_path()
    return managed_db.execute_update(
        "INSERT INTO notification_logs (rule_id, channel, event_type, task_name, status, error_msg) VALUES (?, ?, ?, ?, ?, ?)",
        (rule_id, channel, event_type, task_name, status, error_msg),
    )


def get_notification_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """获取通知日志"""
    return execute_query(
        "SELECT * FROM notification_logs ORDER BY sent_at DESC LIMIT ?", (limit,)
    )


# LLM和VLM配置管理


def set_llm_config(
    model_type: str,  # 'llm' 或 'vlm'
    url: str,
    api_key: str,
    model: str,
    protocol: str = "openai",
    is_active: bool = True,
) -> int:
    """设置LLM或VLM配置"""
    key = f"{model_type}_config"
    value = json.dumps(
        {"url": url, "api_key": api_key, "model": model, "protocol": protocol}
    )
    description = f"{model_type.upper()}配置: {model}"

    _sync_managed_db_path()
    with managed_db.db_manager.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE configs SET value = ?, description = ?, category = ?, model_type = ?, is_active = ?
            WHERE key = ?
        """,
            (value, description, model_type, model_type, 1 if is_active else 0, key),
        )

        if cursor.rowcount == 0:
            cursor.execute(
                """
                INSERT INTO configs (key, value, description, category, model_type, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    key,
                    value,
                    description,
                    model_type,
                    model_type,
                    1 if is_active else 0,
                ),
            )

        row_id = cursor.lastrowid

    return row_id


def get_llm_config(model_type: str = "llm") -> Optional[Dict[str, Any]]:
    """获取LLM或VLM配置"""
    key = f"{model_type}_config"
    query = "SELECT value FROM configs WHERE key = ? AND model_type = ?"
    _sync_managed_db_path()
    result = execute_query(query, (key, model_type))

    if result:
        try:
            return json.loads(result[0]["value"])
        except json.JSONDecodeError:
            return None
    return None


def get_all_llm_configs() -> List[Dict[str, Any]]:
    """获取所有LLM和VLM配置"""
    query = "SELECT key, value, description, model_type, is_active FROM configs WHERE model_type IN ('llm', 'vlm')"
    _sync_managed_db_path()
    results = execute_query(query)

    configs = []
    for row in results:
        try:
            config_data = json.loads(row["value"])
            configs.append(
                {
                    "key": row["key"],
                    "description": row["description"],
                    "model_type": row["model_type"],
                    "is_active": bool(row["is_active"]),
                    **config_data,
                }
            )
        except json.JSONDecodeError:
            continue

    return configs


def set_active_llm_config(model_type: str) -> bool:
    """激活指定类型的LLM配置"""
    _sync_managed_db_path()
    with managed_db.db_manager.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE configs SET is_active = 0 WHERE model_type = ?", (model_type,)
        )
        cursor.execute(
            "UPDATE configs SET is_active = 1 WHERE model_type = ? AND key = ?",
            (model_type, f"{model_type}_config"),
        )
        return cursor.rowcount > 0


# APK管理


def add_apk(
    name: str,
    package_name: str,
    file_path: str,
    version: str = "1.0.0",
    remark: str = "",
    status: str = "completed",
    file_size: int = 0,
    file_hash: str = None,
) -> int:
    """添加APK记录"""
    query = """
        INSERT INTO apks (name, package_name, version, file_path, file_hash, remark, status, file_size)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    return execute_update(
        query,
        (name, package_name, version, file_path, file_hash, remark, status, file_size),
    )


def update_apk_status(apk_id: int, status: str, **kwargs):
    """更新APK状态"""
    kwargs["status"] = status
    return update_apk(apk_id, **kwargs)


def get_apks_page(page: int = 1, size: int = 20, search: str = None) -> tuple:
    """获取APK分页列表（支持 LIKE 搜索）"""
    safe_page = max(page, 1)
    safe_size = max(min(size, 200), 1)
    offset = (safe_page - 1) * safe_size

    count_query = "SELECT COUNT(*) AS total FROM apks"
    data_query = "SELECT * FROM apks"
    params = []

    if search:
        where = " WHERE name LIKE ? OR package_name LIKE ?"
        like = f"%{search}%"
        count_query += where
        data_query += where
        params.extend([like, like])

    data_query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"

    total = execute_query(count_query, tuple(params))
    total_count = int(total[0]["total"]) if total else 0

    items = execute_query(data_query, tuple(params + [safe_size, offset]))

    for apk in items:
        stored_size = apk.get("file_size") or 0
        if stored_size:
            apk["file_size"] = round(float(stored_size) / (1024 * 1024), 2)
            continue

        file_path = apk.get("file_path", "")
        if file_path and os.path.exists(file_path):
            file_size_bytes = os.path.getsize(file_path)
            file_size_mb = round(file_size_bytes / (1024 * 1024), 2)
            apk["file_size"] = file_size_mb
        else:
            apk["file_size"] = 0

    return items, total_count


def get_apks() -> List[Dict[str, Any]]:
    """获取APK列表"""
    query = "SELECT * FROM apks ORDER BY created_at DESC"
    apks = execute_query(query)

    # file_size is stored in bytes at upload time; use it to avoid per-row filesystem stats.
    for apk in apks:
        stored_size = apk.get("file_size") or 0
        if stored_size:
            apk["file_size"] = round(float(stored_size) / (1024 * 1024), 2)
            continue

        file_path = apk.get("file_path", "")
        if file_path and os.path.exists(file_path):
            file_size_bytes = os.path.getsize(file_path)
            file_size_mb = round(file_size_bytes / (1024 * 1024), 2)
            apk["file_size"] = file_size_mb
        else:
            apk["file_size"] = 0

    return apks


def get_apk(apk_id: int) -> Optional[Dict[str, Any]]:
    """获取APK详情"""
    query = "SELECT * FROM apks WHERE id = ?"
    result = execute_query(query, (apk_id,))
    return result[0] if result else None


def update_apk(apk_id: int, **kwargs) -> bool:
    """更新APK信息"""
    keys = kwargs.keys()
    query = f"""
        UPDATE apks SET {", ".join([f"{k} = ?" for k in keys])}
        WHERE id = ?
    """
    params = list(kwargs.values()) + [apk_id]
    _sync_managed_db_path()
    return execute_update(query, tuple(params)) > 0


def delete_apk(apk_id: int) -> bool:
    """删除APK"""
    query = "DELETE FROM apks WHERE id = ?"
    _sync_managed_db_path()
    return execute_update(query, (apk_id,)) > 0


def batch_delete_apks(apk_ids: List[int]) -> int:
    """批量删除APK"""
    placeholders = ",".join(["?" for _ in apk_ids])
    query = f"DELETE FROM apks WHERE id IN ({placeholders})"
    _sync_managed_db_path()
    with managed_db.db_manager.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(query, tuple(apk_ids))
        return cursor.rowcount


# ==================== 仪表盘轻量查询 ====================

DASHBOARD_TASK_STATUS_LABELS = {
    "running": "运行中",
    "finished": "已完成",
    "failed": "失败",
    "pending": "等待中",
    "cancelled": "已取消",
}


def get_dashboard_stats() -> Dict[str, Any]:
    """仪表盘统计 - 使用 COUNT 查询避免加载整表"""
    queries = {
        "total_projects": "SELECT COUNT(*) FROM projects",
        "total_scripts": "SELECT COUNT(*) FROM scripts",
        "total_main_tasks": "SELECT COUNT(*) FROM tasks WHERE task_role != 'child' AND status != 'deleted'",
        "completed_tasks": "SELECT COUNT(*) FROM tasks WHERE status = 'finished' AND task_role != 'child'",
        "running_tasks": "SELECT COUNT(*) FROM tasks WHERE status = 'running' AND task_role != 'child'",
        "failed_tasks": "SELECT COUNT(*) FROM tasks WHERE status IN ('failed','partial_failed') AND task_role != 'child'",
        "total_apks": "SELECT COUNT(*) FROM apks",
    }
    result = {}
    for key, sql in queries.items():
        row = execute_query(sql)
        result[key] = row[0]["COUNT(*)"] if row else 0
    return result


def get_recent_tasks(limit: int = 5) -> List[Dict[str, Any]]:
    """最近任务 - 只取需要的列，SQL侧 LIMIT"""
    query = """
        SELECT id, name, status, created_at, remark, device_id, script_id, project_id,
               task_role, test_type
        FROM tasks
        WHERE task_role != 'child' AND status != 'deleted'
        ORDER BY created_at DESC
        LIMIT ?
    """
    tasks = _attach_device_display_names(execute_query(query, (limit,)))
    for task in tasks:
        task["status_label"] = DASHBOARD_TASK_STATUS_LABELS.get(
            task.get("status"), task.get("status", "")
        )
    return tasks


# ==================== 仪表盘趋势查询函数 ====================


def _build_date_condition(
    days: int = None, start: str = None, end: str = None
) -> tuple:
    """构建日期范围WHERE条件，返回 (sql_fragment, params_tuple)"""
    if start and end:
        return "AND DATE(created_at) BETWEEN ? AND ?", (start, end)
    if days:
        return "AND DATE(created_at) >= DATE('now', ?)", (f"-{days} days",)
    return "AND DATE(created_at) >= DATE('now', '-7 days')", ()


def get_success_rate_trend(
    days: int = 7, start: str = None, end: str = None
) -> List[Dict[str, Any]]:
    """成功率趋势 - 按天统计任务成功/失败数"""
    date_cond, params = _build_date_condition(days, start, end)
    query = f"""
        SELECT
            DATE(created_at) as date,
            COUNT(*) as total,
            SUM(CASE WHEN status = 'finished' THEN 1 ELSE 0 END) as success,
            SUM(CASE WHEN status IN ('failed','partial_failed') THEN 1 ELSE 0 END) as failed,
            ROUND(
                SUM(CASE WHEN status = 'finished' THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0),
                1
            ) as rate
        FROM tasks
        WHERE task_role != 'child' AND status != 'deleted' {date_cond}
        GROUP BY DATE(created_at)
        ORDER BY date
    """
    return execute_query(query, params)


def get_execution_duration_trend(
    days: int = 7, start: str = None, end: str = None
) -> List[Dict[str, Any]]:
    """执行时长趋势 - 按天统计avg/max耗时（p95用max近似，避免N+1）"""
    date_cond, params = _build_date_condition(days, start, end)
    query = f"""
        SELECT
            DATE(created_at) as date,
            ROUND(AVG(
                CASE WHEN completed_at IS NOT NULL AND status = 'finished'
                THEN (CAST(strftime('%s', completed_at) AS REAL) - CAST(strftime('%s', created_at) AS REAL))
                ELSE NULL END
            ), 1) as avg_seconds,
            ROUND(MAX(
                CASE WHEN completed_at IS NOT NULL AND status = 'finished'
                THEN (CAST(strftime('%s', completed_at) AS REAL) - CAST(strftime('%s', created_at) AS REAL))
                ELSE NULL END
            ), 1) as max_seconds,
            COUNT(*) as count
        FROM tasks
        WHERE task_role != 'child' AND status != 'deleted' {date_cond}
        GROUP BY DATE(created_at)
        ORDER BY date
    """
    rows = execute_query(query, params)
    for row in rows:
        row["p95_seconds"] = row.get("max_seconds", 0) or 0
        if "max_seconds" in row:
            del row["max_seconds"]
    return rows


def get_device_activity_trend(
    days: int = 7, start: str = None, end: str = None
) -> List[Dict[str, Any]]:
    """设备活跃度趋势 - 按天+设备统计任务数"""
    date_cond, params = _build_date_condition(days, start, end)
    query = f"""
        SELECT
            DATE(created_at) as date,
            device_id,
            COUNT(*) as task_count
        FROM tasks
        WHERE task_role != 'child' AND status != 'deleted' {date_cond}
        GROUP BY DATE(created_at), device_id
        ORDER BY date
    """
    raw = execute_query(query, params)
    device_ids = []
    for row in raw:
        device_ids.extend(_split_device_ids(row.get("device_id", "")))
    display_map = _get_device_display_map(device_ids)
    by_date = {}
    for row in raw:
        d = row["date"]
        if d not in by_date:
            by_date[d] = {
                "date": d,
                "devices": {},
                "total_active": 0,
                "_device_ids": set(),
            }
        for device_id in _split_device_ids(row.get("device_id", "")):
            by_date[d]["_device_ids"].add(device_id)
            device = display_map.get(device_id, UNKNOWN_DEVICE_DISPLAY_NAME)
            by_date[d]["devices"][device] = (
                by_date[d]["devices"].get(device, 0) + row["task_count"]
            )
    for item in by_date.values():
        item["total_active"] = len(item.pop("_device_ids"))
    return sorted(by_date.values(), key=lambda x: x["date"])


def get_project_heat(
    days: int = 7, start: str = None, end: str = None
) -> List[Dict[str, Any]]:
    """项目执行热力 - 按项目名聚合统计任务数和成功率（TOP 10）"""
    _, raw_params = _build_date_condition(days, start, end)
    if not raw_params:
        raw_params = (f"-{days} days",)

    if start and end:
        tcond = "AND DATE(t.created_at) BETWEEN ? AND ?"
    elif days:
        tcond = "AND DATE(t.created_at) >= DATE('now', ?)"
    else:
        tcond = "AND DATE(t.created_at) >= DATE('now', '-7 days')"

    query = f"""
        SELECT
            p.name,
            COUNT(t.id) as task_count,
            ROUND(
                SUM(CASE WHEN t.status = 'finished' THEN 1 ELSE 0 END) * 100.0
                / NULLIF(COUNT(t.id), 0), 1
            ) as success_rate
        FROM tasks t
        INNER JOIN projects p ON t.project_id = p.id
        WHERE t.task_role != 'child' AND t.status != 'deleted'
            {tcond}
        GROUP BY p.name
        HAVING task_count > 0
        ORDER BY task_count DESC
        LIMIT 10
    """
    return execute_query(query, raw_params)


def get_projects_with_stats(page: int = 1, size: int = 10) -> Dict[str, Any]:
    """获取项目列表（带聚合统计），单条SQL避免N+1"""
    offset = (page - 1) * size
    query = """
        SELECT
            p.id, p.name, p.description, p.created_at, p.updated_at,
            (SELECT COUNT(*) FROM scripts WHERE project_id = p.id) as script_count,
            (SELECT COUNT(*) FROM tasks WHERE project_id = p.id
             AND task_role != 'child' AND status != 'deleted') as task_total,
            (SELECT ROUND(
                SUM(CASE WHEN status = 'finished' THEN 1 ELSE 0 END) * 100.0
                / NULLIF(COUNT(*), 0), 1)
             FROM tasks WHERE project_id = p.id
             AND task_role != 'child' AND status != 'deleted') as task_success_rate,
            (SELECT status FROM tasks WHERE project_id = p.id
             AND task_role != 'child' AND status != 'deleted'
             ORDER BY created_at DESC LIMIT 1) as last_task_status,
            (SELECT completed_at FROM tasks WHERE project_id = p.id
             AND task_role != 'child' AND status != 'deleted'
             ORDER BY created_at DESC LIMIT 1) as last_task_time,
            (SELECT COUNT(*) FROM tasks WHERE project_id = p.id
             AND status IN ('finished', 'failed', 'partial_failed')
             AND (task_role = 'parent' OR (task_role = 'child' AND parent_task_id IS NULL))
            ) as report_count
        FROM projects p
        ORDER BY p.created_at DESC
        LIMIT ? OFFSET ?
    """
    items = execute_query(query, (size, offset))

    total_query = "SELECT COUNT(*) as cnt FROM projects"
    total_result = execute_query(total_query)
    total = total_result[0]["cnt"] if total_result else 0

    return {"items": items, "total": total}


# ==================== 兼容性测试相关函数 ====================


def create_compat_parent_task(
    script_id: int,
    device_ids: List[str],
    remark: str = "",
    project_id: int = None,
    platform: str = "Android",
    compatibility_dimensions: List[str] = None,
    compat_script_ids: List[int] = None,
) -> int:
    """创建兼容性测试父任务"""
    import json
    import time

    task_name = f"兼容性测试_{int(time.time())}"
    device_ids_str = ",".join(device_ids)
    query = """
        INSERT INTO tasks (script_id, device_id, remark, task_text, name, status, 
                          parent_task_id, task_role, platform, project_id, extra, test_type)
        VALUES (?, ?, ?, ?, ?, 'pending', NULL, 'parent', ?, ?, ?, 'ui-compatibility')
    """
    extra = json.dumps(
        {
            "compatibility_dimensions": compatibility_dimensions or [],
            "compat_script_ids": compat_script_ids or [script_id],
        }
    )
    return execute_update(
        query,
        (
            script_id,
            device_ids_str,
            remark,
            task_name,
            task_name,
            platform,
            project_id,
            extra,
        ),
    )


def create_compat_child_task(
    parent_task_id: int,
    script_id: int,
    device_id: str,
    platform: str = "Android",
) -> int:
    """创建兼容性测试子任务"""

    task_name = f"子任务_{parent_task_id}_{device_id}"

    query = """
        INSERT INTO tasks (script_id, device_id, name, status, 
                          parent_task_id, task_role, platform, test_type)
        VALUES (?, ?, ?, 'pending', ?, 'child', ?, 'ui-compatibility')
    """
    return execute_update(
        query, (script_id, device_id, task_name, parent_task_id, platform)
    )


def get_compat_parent_task(parent_task_id: int) -> Optional[Dict[str, Any]]:
    """获取兼容性测试父任务"""
    query = """
        SELECT t.*, s.content as script_content
        FROM tasks t
        LEFT JOIN scripts s ON t.script_id = s.id
        WHERE t.id = ? AND t.task_role = 'parent'
    """
    result = execute_query(query, (parent_task_id,))
    return result[0] if result else None


def get_compat_child_tasks(parent_task_id: int) -> List[Dict[str, Any]]:
    """获取兼容性测试子任务列表"""
    query = """
        SELECT * FROM tasks 
        WHERE parent_task_id = ? AND task_role = 'child'
        ORDER BY created_at ASC
    """
    return execute_query(query, (parent_task_id,))


def get_compat_task_by_device(
    parent_task_id: int, device_id: str
) -> Optional[Dict[str, Any]]:
    """根据设备ID获取子任务"""
    query = """
        SELECT * FROM tasks 
        WHERE parent_task_id = ? AND device_id = ? AND task_role = 'child'
    """
    result = execute_query(query, (parent_task_id, device_id))
    return result[0] if result else None


def insert_task_event(
    task_id: int,
    parent_task_id: int,
    event_type: str,
    dimension: str = None,
    name: str = None,
    status: str = None,
    target: str = None,
    message: str = None,
    severity: str = None,
    step_index: int = None,
    evidence: Any = None,
):
    """插入任务事件"""
    import json

    evidence_json = json.dumps(evidence, ensure_ascii=False) if evidence else None

    query = """
        INSERT INTO task_events (parent_task_id, task_id, event_type, dimension, name, status, 
                                target, message, severity, step_index, evidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """
    execute_update(
        query,
        (
            parent_task_id,
            task_id,
            event_type,
            dimension,
            name,
            status,
            target,
            message,
            severity,
            step_index,
            evidence_json,
        ),
    )


def insert_task_artifact(
    task_id: int,
    parent_task_id: int,
    artifact_type: str,
    relative_path: str = "",
    step_index: int = None,
    assertion_name: str = None,
):
    """插入任务产物"""
    query = """
        INSERT INTO task_artifacts (parent_task_id, task_id, artifact_type, relative_path, 
                                  step_index, assertion_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """
    execute_update(
        query,
        (
            parent_task_id,
            task_id,
            artifact_type,
            relative_path,
            step_index,
            assertion_name,
        ),
    )


def get_task_events(event_id: int = None, task_id: int = None) -> List[Dict[str, Any]]:
    """获取任务事件（支持按事件ID或任务ID查询）"""
    if event_id:
        query = "SELECT * FROM task_events WHERE id = ?"
        return execute_query(query, (event_id,))
    elif task_id:
        query = "SELECT * FROM task_events WHERE task_id = ? ORDER BY created_at ASC"
        return execute_query(query, (task_id,))
    return []


def get_task_events_by_parent(parent_task_id: int) -> List[Dict[str, Any]]:
    """获取父任务下的所有事件"""
    query = """
        SELECT * FROM task_events 
        WHERE parent_task_id = ? 
        ORDER BY created_at ASC
    """
    return execute_query(query, (parent_task_id,))


def get_task_artifacts_by_parent(parent_task_id: int) -> List[Dict[str, Any]]:
    """获取父任务下的所有产物"""
    query = """
        SELECT * FROM task_artifacts 
        WHERE parent_task_id = ? 
        ORDER BY created_at ASC
    """
    return execute_query(query, (parent_task_id,))


def get_assertion_summary(parent_task_id: int) -> Dict[str, Any]:
    """获取断言汇总"""
    query = """
        SELECT dimension, status FROM task_events 
        WHERE parent_task_id = ? AND event_type = 'assertion'
    """
    results = execute_query(query, (parent_task_id,))

    summary = {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "warning": 0,
        "pending_review": 0,
        "by_dimension": {
            "layout": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "warning": 0,
                "pending_review": 0,
            },
            "text": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "warning": 0,
                "pending_review": 0,
            },
            "image": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "warning": 0,
                "pending_review": 0,
            },
            "adaptation": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "warning": 0,
                "pending_review": 0,
            },
            "theme": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "warning": 0,
                "pending_review": 0,
            },
            "interaction": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "warning": 0,
                "pending_review": 0,
            },
            "page_state": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "warning": 0,
                "pending_review": 0,
            },
            "performance": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "warning": 0,
                "pending_review": 0,
            },
            "device": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "warning": 0,
                "pending_review": 0,
            },
        },
    }

    for row in results:
        dimension = row.get("dimension", "unknown")
        status = row.get("status", "")

        if dimension in summary["by_dimension"]:
            summary["total"] += 1
            summary["by_dimension"][dimension]["total"] += 1

            if status == "passed":
                summary["passed"] += 1
                summary["by_dimension"][dimension]["passed"] += 1
            elif status == "failed":
                summary["failed"] += 1
                summary["by_dimension"][dimension]["failed"] += 1
            elif status == "skipped":
                summary["skipped"] += 1
            elif status == "warning":
                summary["warning"] += 1
                summary["by_dimension"][dimension]["warning"] += 1
            elif status == "pending_review":
                summary["pending_review"] += 1
                summary["by_dimension"][dimension]["pending_review"] += 1

    return summary


def upsert_device(
    device_id: str,
    brand: str = None,
    model: str = None,
    os_version: str = None,
    platform: str = "Android",
    resolution: str = None,
    density: str = None,
) -> int:
    """插入或更新设备信息"""
    import datetime

    now = datetime.datetime.now().isoformat()
    query = """
        INSERT INTO devices (device_id, brand, model, os_version, platform, resolution, density, last_seen, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(device_id) DO UPDATE SET
            brand = COALESCE(?, brand),
            model = COALESCE(?, model),
            os_version = COALESCE(?, os_version),
            platform = COALESCE(?, platform),
            resolution = COALESCE(?, resolution),
            density = COALESCE(?, density),
            last_seen = ?,
            updated_at = ?
    """
    execute_update(
        query,
        (
            device_id,
            brand,
            model,
            os_version,
            platform,
            resolution,
            density,
            now,
            now,
            brand,
            model,
            os_version,
            platform,
            resolution,
            density,
            now,
            now,
        ),
    )
    return get_device_id(device_id)


def get_device_id(device_id: str) -> Optional[int]:
    """获取设备ID"""
    query = "SELECT id FROM devices WHERE device_id = ?"
    results = execute_query(query, (device_id,))
    return results[0]["id"] if results else None


def get_device_info(device_id: str) -> Optional[Dict[str, Any]]:
    """获取设备信息"""
    query = "SELECT * FROM devices WHERE device_id = ?"
    results = execute_query(query, (device_id,))
    return results[0] if results else None


def get_all_devices() -> List[Dict[str, Any]]:
    """获取所有设备信息"""
    query = "SELECT * FROM devices ORDER BY last_seen DESC"
    return execute_query(query)


def get_device_list() -> List[Dict[str, str]]:
    """获取设备列表（用于API响应）"""
    query = """
        SELECT device_id, brand, model, os_version, platform, last_seen, status, wifi_ip
        FROM devices
        ORDER BY last_seen DESC
    """
    rows = execute_query(query)
    for row in rows:
        row["device_display_name"] = format_device_display_name(
            row.get("brand"), row.get("model")
        )
    return rows


# ==================== 设备清单同步相关函数 ====================


def _device_attr(device, *names, default=""):
    """Safely extract an attribute from a dict or SimpleNamespace object."""
    for name in names:
        if isinstance(device, dict):
            val = device.get(name)
        else:
            val = getattr(device, name, None)
        if val is not None and val != "":
            return str(val)
    return default


def _normalize_inventory_status(raw_status):
    """Map a raw status string to 'online' or 'offline'."""
    if not raw_status:
        return "online"
    return (
        "online"
        if str(raw_status).lower() in {"device", "online", "connected"}
        else "offline"
    )


def _normalize_discovered_device(device):
    """Convert a discovered device (dict or SimpleNamespace) to a normalized dict."""
    return {
        "device_id": _device_attr(device, "id", "device_id"),
        "status": _normalize_inventory_status(_device_attr(device, "status")),
        "wifi_ip": _device_attr(device, "wifi_ip", "ip", "ip_address"),
        "platform": _device_attr(device, "platform"),
        "model": _device_attr(device, "model"),
        "os_version": _device_attr(device, "os_version", "version", "os"),
        "brand": _device_attr(device, "brand"),
    }


def sync_discovered_devices(devices: list) -> None:
    """Sync discovered devices into the inventory.

    Devices in the list are upserted with their current data.
    Devices already in the DB but absent from the list are marked offline.
    """
    discovered_ids = set()

    for device in devices:
        norm = _normalize_discovered_device(device)
        discovered_ids.add(norm["device_id"])

        query = """
            INSERT INTO devices (device_id, status, wifi_ip, platform, model, os_version, brand, last_seen, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(device_id) DO UPDATE SET
                status = excluded.status,
                wifi_ip = excluded.wifi_ip,
                platform = COALESCE(excluded.platform, platform),
                model = COALESCE(excluded.model, model),
                os_version = COALESCE(excluded.os_version, os_version),
                brand = COALESCE(excluded.brand, brand),
                last_seen = datetime('now'),
                updated_at = datetime('now')
        """
        execute_update(
            query,
            (
                norm["device_id"],
                norm["status"],
                norm["wifi_ip"],
                norm["platform"],
                norm["model"],
                norm["os_version"],
                norm["brand"],
            ),
        )

    if discovered_ids:
        placeholders = ",".join("?" for _ in discovered_ids)
        query = f"""
            UPDATE devices SET status = 'offline', updated_at = datetime('now')
            WHERE device_id NOT IN ({placeholders})
        """
        execute_update(query, tuple(discovered_ids))
    else:
        execute_update("""
            UPDATE devices SET status = 'offline', updated_at = datetime('now')
            WHERE status = 'online'
        """)


def get_device_inventory_counts() -> dict:
    """Return total device count and online device count."""
    result = execute_query("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'online' THEN 1 ELSE 0 END) as online
        FROM devices
    """)
    row = result[0] if result else {"total": 0, "online": 0}
    return {"total": row["total"], "online": row["online"]}


# ==================== VLM 缓存相关函数 ====================


def get_compat_vlm_cache(
    device_id: str, activity: str, step_name: str, dom_hash: str
) -> Optional[Dict[str, Any]]:
    """查询 VLM 缓存（四元组唯一键）"""
    query = """SELECT * FROM compat_vlm_cache
               WHERE device_id = ? AND activity = ? AND step_name = ? AND dom_hash = ?"""
    result = execute_query(query, (device_id, activity, step_name, dom_hash))
    return result[0] if result else None


def create_compat_vlm_cache(
    device_id: str,
    activity: str,
    step_name: str,
    dom_hash: str,
    vlm_result: str,
    screenshot_base64: str = None,
    dimensions: str = None,
) -> int:
    """写入 VLM 缓存（ON CONFLICT DO UPDATE 保留 id，不破坏 FK 引用）"""
    query = """INSERT INTO compat_vlm_cache
               (device_id, activity, step_name, dom_hash, vlm_result, screenshot_base64, dimensions)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(device_id, activity, step_name, dom_hash) DO UPDATE SET
                 vlm_result = excluded.vlm_result,
                 screenshot_base64 = excluded.screenshot_base64,
                 dimensions = excluded.dimensions,
                 created_at = datetime('now')"""
    return execute_update(
        query,
        (
            device_id,
            activity,
            step_name,
            dom_hash,
            vlm_result,
            screenshot_base64,
            dimensions,
        ),
    )


def get_all_compat_vlm_cache() -> List[Dict[str, Any]]:
    """获取所有缓存记录"""
    query = "SELECT * FROM compat_vlm_cache ORDER BY created_at DESC"
    return execute_query(query)


# ==================== UI 兼容性截图分析基线相关函数 ====================


def compute_compat_screenshot_hash(screenshot_base64: str) -> str:
    """Return a perceptual hash for screenshot base64 content.

    Uses pHash so visually identical screenshots produce
    the same hash even with minor differences (e.g. status bar clock).
    """
    return _compute_compat_hashes(screenshot_base64)[0]


def compute_compat_screenshot_hash_sha256(screenshot_base64: str) -> str:
    """Return SHA-256 hash (legacy — for matching pre-migration baselines)."""
    return _compute_compat_hashes(screenshot_base64)[1]


def compute_compat_screenshot_body_hash(screenshot_base64: str) -> str:
    """Return a stable hash for the screenshot body, excluding dynamic edges."""
    import base64
    from io import BytesIO

    try:
        from PIL import Image

        payload = base64.b64decode(screenshot_base64 or "", validate=True)
        img = Image.open(BytesIO(payload)).convert("RGB")
        width, height = img.size
        top = max(1, int(height * 0.08))
        bottom = min(height, int(height * 0.92))
        if bottom <= top:
            return ""
        body = img.crop((0, top, width, bottom))
        return hashlib.sha256(body.tobytes()).hexdigest()
    except Exception:
        return ""


def _compute_compat_hashes(screenshot_base64: str):
    """Compute both pHash (new) and SHA-256 (legacy) from base64 content."""
    import base64
    import hashlib
    from io import BytesIO

    raw = screenshot_base64 or ""
    try:
        payload = base64.b64decode(raw, validate=True)
    except Exception:
        payload = raw.encode("utf-8")

    sha256 = hashlib.sha256(payload).hexdigest()

    try:
        import imagehash
        from PIL import Image

        img = Image.open(BytesIO(payload)).convert("L")
        phash = str(imagehash.phash(img, hash_size=16))
    except Exception:
        phash = sha256

    return phash, sha256


VALID_REVIEW_STATUSES = {"pending", "confirmed", "rejected", "skip", "fixed"}
MANUAL_REVIEW_STATUSES = {"pending", "confirmed", "rejected", "fixed"}


def upsert_compat_analysis_baseline(
    project_id: int,
    device_id: str,
    activity: str,
    step_name: str,
    dom_hash: str,
    screenshot_hash: str,
    vlm_result: str,
    screenshot_base64: str,
    source_parent_task_id: int,
    source_child_task_id: int,
    screenshot_body_hash: str = "",
    annotated_screenshot_base64: str = None,
    review_status: str = "pending",
    remark: str = None,
) -> int:
    """创建或刷新待审核的截图分析基线。"""
    if review_status == "pending" and remark is None:
        try:
            from backend.compatibility.event_parser import safe_json_parse

            parsed = safe_json_parse(vlm_result) if isinstance(vlm_result, str) else vlm_result
            if (
                isinstance(parsed, dict)
                and str(parsed.get("overall_assessment") or "").lower() in {"pass", "passed"}
                and not parsed.get("issues")
            ):
                review_status = "confirmed"
                remark = "pass"
        except Exception:
            pass
    remark = remark or ""
    if review_status not in VALID_REVIEW_STATUSES:
        raise ValueError(f"Invalid baseline review status: {review_status}")
    activity = activity or ""
    step_name = step_name or ""
    dom_hash = dom_hash or ""
    screenshot_hash = screenshot_hash or ""
    screenshot_body_hash = screenshot_body_hash or ""
    query = """
        INSERT INTO compat_analysis_baselines
            (project_id, device_id, activity, step_name, dom_hash, screenshot_hash, screenshot_body_hash,
             vlm_result, screenshot_base64, annotated_screenshot_base64, review_status, remark, source_parent_task_id,
             source_child_task_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(project_id, device_id, activity, step_name, dom_hash, screenshot_hash)
        DO UPDATE SET
            screenshot_body_hash = excluded.screenshot_body_hash,
            vlm_result = excluded.vlm_result,
            screenshot_base64 = excluded.screenshot_base64,
            annotated_screenshot_base64 = COALESCE(excluded.annotated_screenshot_base64, compat_analysis_baselines.annotated_screenshot_base64),
            review_status = excluded.review_status,
            remark = excluded.remark,
            source_parent_task_id = excluded.source_parent_task_id,
            source_child_task_id = excluded.source_child_task_id,
            updated_at = datetime('now')
    """
    execute_update(
        query,
        (
            project_id,
            device_id,
            activity,
            step_name,
            dom_hash,
            screenshot_hash,
            screenshot_body_hash,
            vlm_result,
            screenshot_base64,
            annotated_screenshot_base64,
            review_status,
            remark,
            source_parent_task_id,
            source_child_task_id,
        ),
    )
    row = find_compat_analysis_baseline(
        project_id,
        device_id,
        activity,
        step_name,
        dom_hash,
        screenshot_hash,
        screenshot_body_hash,
    )
    return int(row["id"]) if row else 0


def update_compat_analysis_baseline_annotation(
    id: int, annotated_screenshot_base64: str
) -> bool:
    """Store the VLM-marked screenshot for compatibility analysis list previews."""
    if not id or not annotated_screenshot_base64:
        return False
    execute_update(
        """
        UPDATE compat_analysis_baselines
        SET annotated_screenshot_base64 = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (annotated_screenshot_base64, id),
    )
    return True


def find_compat_analysis_baseline(
    project_id: int,
    device_id: str,
    activity: str,
    step_name: str,
    dom_hash: str = "",
    screenshot_hash: str = "",
    screenshot_body_hash: str = "",
) -> Optional[Dict[str, Any]]:
    """查找匹配的截图分析基线，不限制审核状态。"""
    query = """
        SELECT * FROM compat_analysis_baselines
        WHERE project_id = ?
          AND device_id = ?
          AND activity = ?
          AND step_name = ?
          AND (
            (dom_hash != '' AND dom_hash = ?)
            OR (screenshot_hash != '' AND screenshot_hash = ?)
            OR (screenshot_body_hash != '' AND screenshot_body_hash = ?)
          )
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
    """
    rows = execute_query(
        query,
        (
            project_id,
            device_id,
            activity or "",
            step_name or "",
            dom_hash or "",
            screenshot_hash or "",
            screenshot_body_hash or "",
        ),
    )
    return rows[0] if rows else None


def find_reusable_compat_analysis_baseline(
    project_id: int,
    device_id: str,
    activity: str,
    step_name: str,
    dom_hash: str = "",
    screenshot_hash: str = "",
    screenshot_body_hash: str = "",
) -> Optional[Dict[str, Any]]:
    """查找可复用的截图分析基线。fixed 表示需重新验证，不复用。"""
    baseline = find_compat_analysis_baseline(
        project_id,
        device_id,
        activity,
        step_name,
        dom_hash,
        screenshot_hash,
        screenshot_body_hash,
    )
    if baseline and baseline.get("review_status") != "fixed":
        return baseline
    return None


def update_compat_analysis_baseline_review(
    id: int,
    status: str,
    remark: str = "",
    reviewed_by: str = "",
) -> None:
    """更新截图分析基线的人工审核状态。"""
    if status not in MANUAL_REVIEW_STATUSES:
        raise ValueError(f"Invalid baseline review status: {status}")
    reviewed_at = (
        None if status == "pending" else datetime.now(timezone.utc).isoformat()
    )
    query = """
        UPDATE compat_analysis_baselines
        SET review_status = ?,
            remark = ?,
            reviewed_by = ?,
            reviewed_at = ?,
            updated_at = datetime('now')
        WHERE id = ?
    """
    execute_update(query, (status, remark, reviewed_by, reviewed_at, id))


def delete_compat_analysis_baseline(id: int) -> bool:
    """删除截图分析基线。"""
    existing = execute_query(
        "SELECT id FROM compat_analysis_baselines WHERE id = ?", (id,)
    )
    if not existing:
        return False
    execute_update("DELETE FROM compat_analysis_baselines WHERE id = ?", (id,))
    return True


def _extract_compat_baseline_summary(vlm_result: str) -> Tuple[str, str]:
    """从 vlm_result 中提取严重程度和分析摘要。"""
    if not vlm_result:
        return "info", ""
    try:
        from backend.compatibility.event_parser import safe_json_parse

        data = safe_json_parse(vlm_result)
        if not data:
            return "info", ""
        issues = data.get("issues", [])
        if issues:
            severity = issues[0].get("severity", "info")
            description = issues[0].get("description", "")
            return severity, description
        overall = data.get("overall_assessment", "")
        return "pass" if overall == "pass" else "warning", overall
    except Exception:
        return "info", ""


def list_compat_analysis_baselines(
    project_id: int = None,
    parent_task_id: int = None,
    script_id: int = None,
    system_os: str = None,
    platform: str = None,
    severity: str = None,
    review_status: str = None,
    page: int = 1,
    size: int = 10,
) -> Dict[str, Any]:
    """列出截图分析基线，支持多维度过滤和分页。

    返回结构包含 items、baselines(兼容字段)、total、page、size、total_pages。
    severity 不是数据库列，需在内存过滤后切片分页。
    """
    if size not in (10, 20, 50, 100):
        size = 10
    if page < 1:
        page = 1

    clauses = []
    params = []

    if project_id is not None:
        clauses.append("b.project_id = ?")
        params.append(project_id)
    if parent_task_id is not None:
        clauses.append("b.source_parent_task_id = ?")
        params.append(parent_task_id)
    if script_id is not None:
        clauses.append("t.script_id = ?")
        params.append(script_id)
    if system_os is not None:
        clauses.append("s.system_os = ?")
        params.append(system_os)
    if platform is not None:
        clauses.append("b.platform = ?")
        params.append(platform)
    if review_status is not None:
        clauses.append("b.review_status = ?")
        params.append(review_status)

    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    query = f"""
        SELECT b.*, p.name as project_name, s.name as script_name, s.system_os,
               d.brand as device_brand, d.model as device_model
        FROM compat_analysis_baselines b
        LEFT JOIN projects p ON b.project_id = p.id
        LEFT JOIN tasks t ON b.source_parent_task_id = t.id
        LEFT JOIN scripts s ON t.script_id = s.id
        LEFT JOIN devices d ON b.device_id = d.device_id
        {where_clause}
        ORDER BY b.created_at DESC, b.reviewed_at DESC, b.id DESC
    """

    rows = execute_query(query, tuple(params))

    # severity 从 vlm_result 解析，需在内存过滤
    for row in rows:
        row["severity"], row["analysis_summary"] = _extract_compat_baseline_summary(
            row.get("vlm_result", "")
        )
        brand = (row.get("device_brand") or "").strip()
        model = (row.get("device_model") or "").strip()
        row["device_brand_model"] = (
            "/".join(part for part in (brand, model) if part) or "-"
        )
        row["preview_screenshot_base64"] = (
            row.get("annotated_screenshot_base64") or row.get("screenshot_base64") or ""
        )

    if severity:
        rows = [row for row in rows if row["severity"] == severity]

    total = len(rows)
    review_status_counts = {"pending": 0, "confirmed": 0, "rejected": 0, "skip": 0}
    for row in rows:
        status = row.get("review_status", "pending")
        if status in review_status_counts:
            review_status_counts[status] += 1

    total_pages = max(1, (total + size - 1) // size)
    if page > total_pages:
        page = total_pages
    start = (page - 1) * size
    page_rows = rows[start : start + size]

    return {
        "items": page_rows,
        "baselines": page_rows,  # 兼容旧前端
        "total": total,
        "review_status_counts": review_status_counts,
        "page": page,
        "size": size,
        "total_pages": total_pages,
    }


# ==================== 审核工单相关函数 ====================


def get_audit_items(parent_task_id: int) -> List[Dict[str, Any]]:
    """获取指定父任务下的所有审核工单"""
    query = """SELECT * FROM compat_audit_items WHERE parent_task_id = ? ORDER BY created_at"""
    return execute_query(query, (parent_task_id,))


def get_all_audit_items(status: str = None) -> List[Dict[str, Any]]:
    """获取所有审核工单（可选按状态筛选）"""
    if status:
        query = (
            "SELECT * FROM compat_audit_items WHERE status = ? ORDER BY created_at DESC"
        )
        return execute_query(query, (status,))
    query = "SELECT * FROM compat_audit_items ORDER BY created_at DESC"
    return execute_query(query)


def get_audit_item(id: int) -> Optional[Dict[str, Any]]:
    """获取单个审核工单"""
    query = "SELECT * FROM compat_audit_items WHERE id = ?"
    result = execute_query(query, (id,))
    return result[0] if result else None


def create_audit_item(
    cache_id: int,
    parent_task_id: int,
    child_task_id: int,
    issue_type: str,
    issue_detail: str,
    first_seen_task_id: int = None,
) -> int:
    """创建审核工单"""
    fst = first_seen_task_id or parent_task_id
    query = """INSERT INTO compat_audit_items
               (cache_id, parent_task_id, child_task_id, issue_type, issue_detail, first_seen_task_id)
               VALUES (?, ?, ?, ?, ?, ?)"""
    return execute_update(
        query, (cache_id, parent_task_id, child_task_id, issue_type, issue_detail, fst)
    )


def update_audit_item(
    id: int, status: str, remark: str = None, reviewed_by: str = None
):
    """更新审核工单状态"""
    reviewed_at = datetime.now(timezone.utc).isoformat()
    fields = ["status = ?", "reviewed_at = ?"]
    params = [status, reviewed_at]
    if remark is not None:
        fields.append("remark = ?")
        params.append(remark)
    if reviewed_by is not None:
        fields.append("reviewed_by = ?")
        params.append(reviewed_by)
    params.append(id)
    query = f"UPDATE compat_audit_items SET {', '.join(fields)} WHERE id = ?"
    execute_update(query, tuple(params))


def get_audit_items_by_cache(cache_id: int) -> List[Dict[str, Any]]:
    """获取指定缓存记录关联的所有审核工单"""
    query = (
        "SELECT * FROM compat_audit_items WHERE cache_id = ? ORDER BY created_at DESC"
    )
    return execute_query(query, (cache_id,))


def get_issue_history(
    parent_task_id: int, issue_type: str, issue_detail: str
) -> List[Dict[str, Any]]:
    """
    获取指定问题的历史时间线，展示该问题在多次运行中的演变

    Args:
        parent_task_id: 父任务ID
        issue_type: 问题类型
        issue_detail: 问题详情

    Returns:
        按运行时间排序的问题历史列表，包含每个 run 中该问题的状态和截图信息
    """
    # 查询该父任务下所有相关的问题（按 issue_type + issue_detail 匹配）
    query = """
        SELECT 
            ai.id,
            ai.cache_id,
            ai.child_task_id,
            ai.issue_type,
            ai.issue_detail,
            ai.status,
            ai.remark,
            ai.reviewed_by,
            ai.reviewed_at,
            ai.created_at as audit_created_at,
            ai.first_seen_task_id,
            ai.parent_task_id,
            cvc.created_at as run_created_at,
            cvc.screenshot_base64,
            cvc.dom_hash,
            cvc.step_name
        FROM compat_audit_items ai
        LEFT JOIN compat_vlm_cache cvc ON ai.cache_id = cvc.id
        WHERE ai.parent_task_id = ? 
            AND ai.issue_type = ?
            AND ai.issue_detail = ?
        ORDER BY ai.created_at ASC
    """
    return execute_query(query, (parent_task_id, issue_type, issue_detail))


def get_issue_progression(parent_task_id: int) -> List[Dict[str, Any]]:
    """
    获取所有问题在多次运行中的进展历史，按 issue_key 分组

    Args:
        parent_task_id: 父任务ID

    Returns:
        每个问题的历史记录列表
    """
    query = """
        SELECT 
            ai.issue_type,
            ai.issue_detail,
            ai.first_seen_task_id,
            ai.status as current_status,
            ai.remark,
            ai.reviewed_by,
            ai.reviewed_at,
            ai.created_at as last_updated,
            ai.parent_task_id,
            COUNT(ai.id) as occurrence_count,
            MIN(ai.created_at) as first_seen_at,
            MAX(ai.created_at) as last_seen_at,
            GROUP_CONCAT(DISTINCT ai.status) as status_history
        FROM compat_audit_items ai
        WHERE ai.parent_task_id = ?
        GROUP BY ai.issue_type, ai.issue_detail
        ORDER BY first_seen_at ASC
    """
    return execute_query(query, (parent_task_id,))


# ==================== DOM 签名采集函数 ====================


def get_captures_with_dom(task_id: int) -> List[Dict[str, Any]]:
    """获取指定任务的截图采集事件（含 DOM 签名）"""
    query = """SELECT te.* FROM task_events te
               WHERE te.task_id = ? AND te.event_type = 'dom_signature'
               ORDER BY te.step_index ASC"""
    return execute_query(query, (task_id,))


# 数据库初始化已移至 web_ui/main.py 的 lifespan 中
