"""仪表盘路由 - 性能优化版"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

import asyncio
import time

from backend.db.connection_manager import db_manager
from backend.db.database import (
    _sync_managed_db_path,
    get_device_activity_trend,
    get_device_inventory_counts,
    get_execution_duration_trend,
    get_project_heat,
    get_recent_tasks,
    get_success_rate_trend,
)
from backend.mcp.mcp_tools import mcp_tools
from web_ui.routes.devices import device_to_response_dict, discover_devices_cached


@router.get("/stats")
async def api_dashboard_stats():
    """获取仪表盘统计数据（单次SQL批量COUNT + 设备清单计数）"""
    db_stats, inv_counts = await asyncio.gather(
        asyncio.to_thread(_batch_stats_query),
        asyncio.to_thread(get_device_inventory_counts),
    )

    return {
        "total_projects": db_stats.get("total_projects", 0),
        "total_scripts": db_stats.get("total_scripts", 0),
        "total_tasks": db_stats.get("total_main_tasks", 0),
        "total_devices": inv_counts.get("total", 0),
        "online_devices": inv_counts.get("online", 0),
        "active_apks": db_stats.get("total_apks", 0),
    }


@router.get("/recent-tasks")
async def api_dashboard_recent_tasks(limit: int = 5):
    """获取最近任务列表（只取必要列，SQL侧LIMIT）"""
    tasks = await asyncio.to_thread(get_recent_tasks, min(limit, 20))
    return tasks


@router.get("/device-status")
async def api_dashboard_device_status():
    """获取设备状态概览"""
    devices = await discover_devices_cached()
    return {
        "devices": [device_to_response_dict(d) for d in devices],
        "current_device": mcp_tools.current_device,
    }


@router.get("/trends")
async def api_dashboard_trends(days: int = 7, start: str = None, end: str = None):
    """获取趋势数据 - 单线程批量执行（SQLite单连接，并行无收益）"""
    t0 = time.monotonic()
    result = await asyncio.to_thread(_batch_trends_query, days, start, end)
    elapsed = time.monotonic() - t0
    if elapsed > 1:
        import logging
        logging.getLogger("dashboard").warning(f"trends API took {elapsed:.1f}s (days={days})")

    return result


def _batch_trends_query(days: int, start: str, end: str):
    """单线程中顺序执行4条趋势查询，避免线程池+锁开销"""
    _sync_managed_db_path()
    return {
        "success_rate": get_success_rate_trend(days, start, end),
        "execution_duration": get_execution_duration_trend(days, start, end),
        "device_activity": get_device_activity_trend(days, start, end),
        "project_heat": get_project_heat(days, start, end),
    }


def _batch_stats_query():
    """单次连接、一条SQL执行所有COUNT，避免4次整表加载"""
    _sync_managed_db_path()
    sql = """
        SELECT
            (SELECT COUNT(*) FROM projects) AS total_projects,
            (SELECT COUNT(*) FROM scripts) AS total_scripts,
            (SELECT COUNT(*) FROM tasks WHERE task_role != 'child' AND status != 'deleted') AS total_main_tasks,
            (SELECT COUNT(*) FROM apks) AS total_apks
    """
    result = db_manager.execute_query(sql)
    return result[0] if result else {}
