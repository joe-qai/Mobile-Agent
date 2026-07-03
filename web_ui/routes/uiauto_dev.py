"""uiauto.dev 服务启动路由"""

import atexit
import logging
import shutil
import subprocess
import sys

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/agent/uiauto-dev", tags=["uiauto-dev"])

logger = logging.getLogger(__name__)

# 模块级进程句柄，避免重复启动
_process = None


def _cleanup_uiauto_dev():
    """Web 服务退出时终止 uiauto.dev 进程"""
    global _process
    if _process and _process.poll() is None:
        try:
            _process.terminate()
        except Exception:
            pass


atexit.register(_cleanup_uiauto_dev)


@router.post("/start")
async def start_uiauto_dev():
    """后台启动 uiauto.dev 服务"""
    global _process

    # 已在运行
    if _process and _process.poll() is None:
        return {"success": True, "url": "http://127.0.0.1:20242", "message": "已在运行"}

    # 检测是否安装，并拿到可执行文件完整路径
    exe_path = shutil.which("uiauto.dev")
    if exe_path is None:
        return JSONResponse(
            status_code=500,
            content={"detail": "pip install uiautodev"},
        )

    # 直接启动可执行文件（不经过 shell），进程句柄即 uiauto.dev 本身
    try:
        kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        # Windows 下隐藏命令行窗口
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        _process = subprocess.Popen([exe_path], **kwargs)
        logger.info("uiauto.dev 进程已启动, pid=%s", _process.pid)
        return {"success": True, "url": "http://127.0.0.1:20242"}
    except Exception as e:
        logger.exception("启动 uiauto.dev 失败")
        return JSONResponse(
            status_code=500,
            content={"detail": f"启动失败: {e}"},
        )


@router.post("/stop")
async def stop_uiauto_dev():
    """停止 uiauto.dev 服务（杀整个进程树）"""
    global _process

    if not _process or _process.poll() is not None:
        _process = None
        return {"success": True, "message": "未在运行"}

    pid = _process.pid

    # Windows: taskkill /F /T /PID 终止进程及其所有子进程
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            logger.exception("taskkill 失败, 回退 terminate")
            try:
                _process.terminate()
                _process.wait(timeout=5)
            except Exception:
                pass
    else:
        try:
            _process.terminate()
            _process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                _process.kill()
                _process.wait(timeout=3)
            except Exception:
                pass
        except Exception:
            pass

    _process = None
    return {"success": True}


@router.get("/status")
async def get_uiauto_dev_status():
    """查询 uiauto.dev 运行状态"""
    running = _process is not None and _process.poll() is None
    return {"running": running, "url": "http://127.0.0.1:20242" if running else None}
