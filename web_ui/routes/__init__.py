"""
Web UI 路由汇总模块
从各子模块导入路由并注册到 FastAPI app
"""

from fastapi import FastAPI


def register_routes(app: FastAPI):
    """
    注册所有路由到 FastAPI 应用

    这个函数应该在 main.py 中调用，确保所有路由正确注册
    """
    from web_ui.routes import (
        apks,
        compatibility,
        configs,
        dashboard,
        devices,
        pages,
        projects,
        reports,
        scripts,
        settings,
        tasks,
        uiauto_dev,
        websocket,
    )

    app.include_router(pages.router)
    app.include_router(dashboard.router)
    app.include_router(projects.router)
    app.include_router(scripts.router)
    app.include_router(tasks.router)
    app.include_router(devices.router)
    app.include_router(apks.router)
    app.include_router(reports.router)
    app.include_router(configs.router)
    app.include_router(settings.router)
    app.include_router(settings.init_agent_router)
    app.include_router(compatibility.router)
    app.include_router(uiauto_dev.router)
    app.include_router(websocket.router)


__all__ = [
    "pages",
    "projects",
    "scripts",
    "tasks",
    "devices",
    "apks",
    "reports",
    "configs",
    "settings",
    "compatibility",
    "websocket",
    "dashboard",
    "uiauto_dev",
    "register_routes",
    "init_agent_router",
]
