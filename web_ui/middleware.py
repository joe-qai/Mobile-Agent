"""中间件模块"""
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class LargeFileUploadMiddleware(BaseHTTPMiddleware):
    """支持大文件上传的中间件"""
    async def dispatch(self, request: Request, call_next):
        if request.method == "POST" and "multipart/form-data" in request.headers.get("content-type", ""):
            request.scope["timeout"] = 300
        response: Response = await call_next(request)
        return response


class SkipAccessLogMiddleware(BaseHTTPMiddleware):
    """跳过特定路径的访问日志（如 scrcpy 轮询接口）"""
    SKIP_PATHS = {"/api/devices/screen"}
    
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        if request.url.path in self.SKIP_PATHS:
            response.headers["X-Skip-Log"] = "true"
        return response