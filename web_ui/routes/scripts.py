"""脚本路由"""
import asyncio
import json
import os
import urllib.parse
import zipfile
from io import BytesIO
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from starlette.responses import Response

router = APIRouter(prefix="/api/scripts", tags=["scripts"])

from backend.db.database import (
    batch_delete_scripts,
    count_scripts,
    create_script,
    delete_script,
    get_script,
    get_scripts_page,
    update_script,
)
from web_ui.models.schemas import (
    BatchDelete,
    PreviewFromAgent,
    SaveFromPreview,
    ScriptCreate,
)


@router.get("/")
async def api_get_scripts(page: int = 1, size: int = 10, source: Optional[str] = None, system_os: Optional[str] = None, project_id: Optional[int] = None):
    """获取脚本列表（支持分页、来源、系统和项目筛选）"""
    items, total = await asyncio.gather(
        asyncio.to_thread(get_scripts_page, page=page, size=size, source=source, system_os=system_os, project_id=project_id),
        asyncio.to_thread(count_scripts, source=source, system_os=system_os, project_id=project_id),
    )
    return {"items": items, "total": total}


@router.get("/{script_id}")
async def api_get_script(script_id: int):
    """获取脚本详情"""
    script = get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")
    return script


@router.post("/")
async def api_create_script(data: ScriptCreate):
    """创建脚本"""
    result = create_script(data.name, data.content, data.version, project_id=data.project_id, system_os=data.system_os, allow_duplicate=True)
    return {"success": result.get("success", True), "id": result.get("script_id"), "name": data.name, "message": result.get("message", "")}


@router.put("/{script_id}")
async def api_update_script(script_id: int, data: ScriptCreate):
    """更新脚本"""
    success = update_script(script_id, data.name, data.content, data.version, project_id=data.project_id, system_os=data.system_os)
    if not success:
        raise HTTPException(status_code=404, detail="Script not found")
    return {"success": True}


@router.delete("/{script_id}")
async def api_delete_script(script_id: int):
    """删除脚本"""
    success = delete_script(script_id)
    if not success:
        raise HTTPException(status_code=404, detail="Script not found")
    return {"success": True}


@router.get("/{script_id}/download")
async def api_download_script(script_id: int, filename: Optional[str] = None):
    """下载单个脚本"""
    script = get_script(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")
    
    content = script.get("content", "")
    
    if content is None:
        content = ""
    
    if isinstance(content, bytes):
        content = content.decode('utf-8')
    
    if not isinstance(content, str):
        try:
            content = json.dumps(content, ensure_ascii=False, indent=2)
        except:
            content = str(content)
    
    if filename:
        filename = os.path.basename(filename)
        if not os.path.splitext(filename)[1]:
            filename += '.py'
    else:
        safe_name = os.path.basename(script.get('name', 'script'))
        filename = f"{safe_name}.py"
    
    encoded_filename = urllib.parse.quote(filename)
    
    return Response(
        content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{encoded_filename}"; filename*=UTF-8\'\'\'\'{encoded_filename}',
            "Content-Type": "text/plain; charset=utf-8"
        }
    )


@router.post("/batch-download")
async def api_batch_download_scripts(data: BatchDelete):
    """批量下载脚本（打包成ZIP）"""
    output = BytesIO()
    
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for script_id in data.ids:
            script = get_script(script_id)
            if script:
                content = script.get("content", "")
                
                if content is None:
                    content = ""
                
                if isinstance(content, bytes):
                    content = content.decode('utf-8')
                
                if not isinstance(content, str):
                    try:
                        content = json.dumps(content, ensure_ascii=False, indent=2)
                    except:
                        content = str(content)
                
                safe_name = os.path.basename(script.get('name', f'script_{script_id}'))
                filename = f"{safe_name}.py"
                zipf.writestr(filename, content)
    
    output.seek(0)
    zip_data = output.getvalue()
    
    return Response(
        zip_data,
        media_type="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=scripts.zip",
            "Content-Type": "application/zip",
            "Content-Length": str(len(zip_data))
        }
    )


@router.post("/batch-delete")
async def api_batch_delete_scripts(data: BatchDelete):
    """批量删除脚本"""
    count = batch_delete_scripts(data.ids)
    return {"success": True, "deleted_count": count}


@router.post("/upload")
async def api_upload_script(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    content: Optional[str] = Form(None),
    system_os: Optional[str] = Form("Android"),
    project_id: Optional[str] = Form(None),
):
    """上传脚本文件"""
    if not file.filename.endswith(".py"):
        raise HTTPException(status_code=400, detail="Only Python files are allowed")

    if content:
        content_str = content
    else:
        file_content = await file.read()
        content_str = file_content.decode("utf-8")

    script_name = name if name else os.path.splitext(file.filename)[0]
    pid = int(project_id) if project_id and project_id.isdigit() else None
    result = create_script(
        script_name, content_str, file_path="", source="local", system_os=system_os, project_id=pid
    )
    return {
        "success": result.get("success", True), 
        "id": result.get("script_id"), 
        "name": result.get("final_name", script_name),
        "message": result.get("message")
    }


@router.post("/preview-from-agent")
async def api_preview_script_from_agent(data: PreviewFromAgent):
    """根据Agent执行步骤生成脚本预览（使用LLM生成）"""
    from backend.utils.script_generator import generate_script

    content = await asyncio.to_thread(
        generate_script,
        data.task_text, 
        data.step_results, 
        data.device_type or "adb",
        True,
        True,
        "normal",  # test_type
        data.page_changes,  # 传递页面变化记录
    )
    if not content:
        return {"success": False, "error": "无法生成脚本内容"}
    return {"success": True, "content": content}


@router.post("/save-from-preview")
async def api_save_script_from_preview(data: SaveFromPreview):
    """保存从预览生成的脚本（带防重检查）"""
    if not data.project_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="必须选择所属项目")
    result = create_script(
        name=data.name,
        content=data.content,
        version="1.0.0",
        source="agent",
        allow_duplicate=False,
        system_os=data.system_os,
        project_id=data.project_id,
    )
    
    if result['success']:
        return {
            "success": True,
            "script_id": result['script_id'],
            "message": result['message'],
            "final_name": result.get('final_name', data.name),
            "is_duplicate": False
        }
    else:
        return {
            "success": False,
            "script_id": result['script_id'],
            "message": result['message'],
            "is_duplicate": True,
            "duplicate_type": result.get('duplicate_type', 'content')
        }
