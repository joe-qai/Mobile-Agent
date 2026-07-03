"""FastAPI主应用 - 提供所有API接口和页面路由"""

import os
import sys

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import asyncio
import logging
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from backend.agent.agent_manager import agent_manager
from web_ui.middleware import LargeFileUploadMiddleware, SkipAccessLogMiddleware
from web_ui.routes import register_routes
from web_ui.utils.logger import log_callback, process_log_queue


async def lifespan(app: FastAPI):
    from backend.db.database import init_db
    init_db()
    logging.info("数据库初始化完成")

    from backend.notification import notification_dispatcher
    notification_dispatcher.init_default_rules()
    logging.info("通知规则初始化完成")

    asyncio.create_task(process_log_queue())
    logging.info("日志队列处理任务已启动")
    yield


app = FastAPI(
    title="Mobile Agent Platform",
    version="3.0",
    lifespan=lifespan,
)

app.state.max_request_size = None

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return templates.TemplateResponse(request, "error.html", {"code": 404, "message": "页面未找到"}, status_code=404)


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    return templates.TemplateResponse(request, "error.html", {"code": 500, "message": "服务器内部错误"}, status_code=500)

app.add_middleware(LargeFileUploadMiddleware)
app.add_middleware(SkipAccessLogMiddleware)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

ARTIFACTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "artifacts")

if not os.path.exists(ARTIFACTS_DIR):
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)

app.mount("/artifacts", StaticFiles(directory=ARTIFACTS_DIR), name="artifacts")

agent_manager.set_log_callback(log_callback)

register_routes(app)


if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Mobile Agent Platform")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind")
    parser.add_argument("--port", type=int, default=8001, help="Port to bind")
    parser.add_argument("--ssl-keyfile", help="SSL key file path for HTTPS")
    parser.add_argument("--ssl-certfile", help="SSL certificate file path for HTTPS")
    parser.add_argument("--workers", type=int, default=1 if sys.platform == "win32" else 2, help="Number of worker processes")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload on code changes")
    args = parser.parse_args()

    os.environ["WEB_UI_PORT"] = str(args.port)
    os.environ["WEB_UI_SCHEME"] = "https" if args.ssl_keyfile and args.ssl_certfile else "http"

    uvicorn_kwargs = {
        "host": args.host,
        "port": args.port,
        "reload": args.reload,
        "workers": args.workers if not args.reload else 1,
    }

    if args.ssl_keyfile and args.ssl_certfile:
        uvicorn_kwargs.update({
            "ssl_keyfile": args.ssl_keyfile,
            "ssl_certfile": args.ssl_certfile,
        })
        logging.info(f"Starting HTTPS server on https://{args.host}:{args.port}")
    else:
        logging.info(f"Starting HTTP server on http://{args.host}:{args.port}")

    uvicorn.run("web_ui.main:app", **uvicorn_kwargs)
