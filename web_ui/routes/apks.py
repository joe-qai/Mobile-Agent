"""APK 管理路由"""

import asyncio
import hashlib
import os
import shutil
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from backend.db.database import (
    add_apk,
    delete_apk,
    get_apk,
    get_apks,
    get_apks_page,
    update_apk,
)
from backend.mcp.mcp_tools import mcp_tools
from web_ui.utils.apk import extract_apk_package
from web_ui.utils.helpers import get_apks_base_dir

router = APIRouter(prefix="/api/apks", tags=["apks"])

# 分片上传临时目录
CHUNKS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "uploads", "chunks"
)


def _ensure_no_apk_filename_conflict(
    file_name: str, file_hash: str, apks: Optional[list] = None
):
    """Reject same final filename for different APK content."""
    target_name = os.path.basename(file_name)
    for apk in apks if apks is not None else get_apks():
        existing_name = os.path.basename(apk.get("file_path", ""))
        if existing_name == target_name and apk.get("file_hash") != file_hash:
            raise HTTPException(
                status_code=409,
                detail="同名APK已存在，请重命名后上传",
            )


@router.get("/")
async def api_list_apks(
    search: Optional[str] = None,
    package_name: Optional[str] = None,
    page: Optional[int] = Query(None),
    size: Optional[int] = Query(None),
):
    """获取APK列表（支持搜索、包名过滤和分页）"""
    if page is not None:
        safe_size = size or 10
        items, total = await asyncio.to_thread(
            get_apks_page, page=page, size=safe_size, search=search
        )
        if package_name:
            items = [a for a in items if a.get("package_name") == package_name]
        return {"items": items, "total": total, "page": page, "size": safe_size}

    apks = get_apks()

    if search:
        apks = [a for a in apks if search.lower() in a.get("name", "").lower()]
    if package_name:
        apks = [a for a in apks if a.get("package_name") == package_name]

    return apks


@router.get("/check-hash/{file_hash}")
async def api_check_hash(file_hash: str):
    """检查文件哈希是否存在"""
    apks = get_apks()
    for apk in apks:
        if apk.get("file_hash") == file_hash:
            return {"exists": True, "apk": apk}
    return {"exists": False, "apk": None}


class MergeChunksRequest(BaseModel):
    """分片合并请求体"""

    upload_id: str
    file_hash: str
    file_name: str
    name: Optional[str] = None
    version: Optional[str] = None
    remark: Optional[str] = None


@router.post("/chunk")
async def api_upload_chunk(
    chunk: UploadFile = File(...),
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    file_name: Optional[str] = Form(None),
    file_hash: Optional[str] = Form(None),
):
    """接收单个分片数据，存储到临时目录"""
    upload_dir = os.path.join(CHUNKS_DIR, upload_id)
    os.makedirs(upload_dir, exist_ok=True)

    chunk_path = os.path.join(upload_dir, f"{chunk_index}.part")
    content = await chunk.read()
    with open(chunk_path, "wb") as f:
        f.write(content)

    return {"success": True, "chunk_index": chunk_index}


@router.get("/chunks/{upload_id}")
async def api_get_uploaded_chunks(upload_id: str):
    """查询指定 upload_id 已上传的分片索引列表（断点续传）"""
    upload_dir = os.path.join(CHUNKS_DIR, upload_id)
    if not os.path.exists(upload_dir):
        return {"uploaded": []}

    uploaded = []
    for name in os.listdir(upload_dir):
        if name.endswith(".part"):
            try:
                idx = int(name[:-5])  # 去掉 .part 后缀
                uploaded.append(idx)
            except ValueError:
                continue
    uploaded.sort()
    return {"uploaded": uploaded}


@router.post("/merge")
async def api_merge_chunks(body: MergeChunksRequest):
    """合并所有分片为完整 APK 文件，校验哈希，写入数据库并触发后台解析"""
    upload_id = body.upload_id
    file_hash = body.file_hash
    file_name = body.file_name
    app_name = body.name
    version = body.version or "1.0.0"
    remark = body.remark or ""

    upload_dir = os.path.join(CHUNKS_DIR, upload_id)
    if not os.path.exists(upload_dir):
        raise HTTPException(status_code=404, detail="上传会话不存在")

    # 扫描分片文件
    part_files = []
    for name in os.listdir(upload_dir):
        if name.endswith(".part"):
            try:
                idx = int(name[:-5])
                part_files.append((idx, os.path.join(upload_dir, name)))
            except ValueError:
                continue
    part_files.sort(key=lambda x: x[0])

    if not part_files:
        raise HTTPException(status_code=400, detail="没有找到分片文件")

    # 合并到临时文件并计算哈希
    merged_path = os.path.join(upload_dir, file_name)
    hasher = hashlib.sha256()
    with open(merged_path, "wb") as out:
        for _, part_path in part_files:
            with open(part_path, "rb") as pf:
                while True:
                    buf = pf.read(1024 * 1024)  # 1MB
                    if not buf:
                        break
                    out.write(buf)
                    hasher.update(buf)

    # 哈希校验
    actual_hash = hasher.hexdigest()
    if actual_hash != file_hash:
        raise HTTPException(
            status_code=400,
            detail=f"哈希校验失败: 期望 {file_hash}, 实际 {actual_hash}",
        )

    _ensure_no_apk_filename_conflict(file_name, file_hash)

    # 保存到 APK 存储目录
    apks_dir = get_apks_base_dir()
    os.makedirs(apks_dir, exist_ok=True)
    final_path = os.path.join(apks_dir, os.path.basename(file_name))
    os.replace(merged_path, final_path)

    file_size = os.path.getsize(final_path)
    app_name = app_name or os.path.splitext(file_name)[0]

    apk_id = add_apk(
        name=app_name,
        package_name="",
        file_path=final_path,
        version=version,
        remark=remark,
        status="uploading",
        file_size=file_size,
        file_hash=file_hash,
    )

    asyncio.create_task(
        _process_apk_after_upload(apk_id, final_path, app_name, version)
    )

    # 清理临时分片目录
    try:
        shutil.rmtree(upload_dir)
    except Exception:
        pass

    return {"success": True, "apk_id": apk_id}


@router.get("/{apk_id}")
async def api_get_apk(apk_id: int):
    """获取APK详情"""
    apk = get_apk(apk_id)
    if not apk:
        raise HTTPException(status_code=404, detail="APK不存在")
    return apk


@router.post("/")
async def api_upload_apk(
    file: UploadFile = File(...),
    app_name: Optional[str] = Form(None),
    version: Optional[str] = Form(None),
    package_name: Optional[str] = Form(None),
    remark: Optional[str] = Form(None),
):
    """上传APK文件"""
    if not file.filename.endswith(".apk"):
        raise HTTPException(status_code=400, detail="只能上传APK文件")

    content = await file.read()
    file_hash = hashlib.sha256(content).hexdigest()

    apks = get_apks()
    for apk in apks:
        if apk.get("file_hash") == file_hash:
            return {"success": True, "apk_id": apk["id"], "message": "APK已存在"}

    _ensure_no_apk_filename_conflict(file.filename, file_hash, apks)

    apks_dir = get_apks_base_dir()
    os.makedirs(apks_dir, exist_ok=True)
    file_path = os.path.join(apks_dir, os.path.basename(file.filename))

    with open(file_path, "wb") as f:
        f.write(content)

    file_size = os.path.getsize(file_path)
    app_name = app_name or os.path.splitext(file.filename)[0]
    apk_version = version or "1.0.0"
    apk_remark = remark or ""

    apk_id = add_apk(
        name=app_name,
        package_name="",
        file_path=file_path,
        version=apk_version,
        remark=apk_remark,
        status="uploading",
        file_size=file_size,
        file_hash=file_hash,
    )

    asyncio.create_task(
        _process_apk_after_upload(apk_id, file_path, app_name, apk_version)
    )

    return {"success": True, "apk_id": apk_id}


async def _process_apk_after_upload(
    apk_id: int, file_path: str, app_name: str, version: str
):
    """后台处理已上传的APK文件（解析包名、更新状态）"""
    try:
        file_size = os.path.getsize(file_path)

        try:
            pkg_name = extract_apk_package(file_path)
        except RuntimeError as e:
            update_apk(
                apk_id, status="failed", package_name="", remark=f"解析失败: {str(e)}"
            )
            return

        if pkg_name is None:
            pkg_name = ""

        apk_version = version if version else "1.0.0"

        update_apk(
            apk_id,
            package_name=pkg_name,
            version=apk_version,
            status="completed",
            file_size=file_size,
        )

        from backend.config.apps import add_package_mapping as _backend_mapping

        _backend_mapping(app_name, pkg_name)



    except Exception as e:
        update_apk(apk_id, status="failed", remark=f"处理失败: {str(e)}")


@router.put("/{apk_id}")
async def api_update_apk(
    apk_id: int,
    app_name: Optional[str] = Form(None),
    version: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
):
    """更新APK信息"""
    apk = get_apk(apk_id)
    if not apk:
        raise HTTPException(status_code=404, detail="APK不存在")

    update_data = {}
    if app_name is not None:
        update_data["name"] = app_name
    if version is not None:
        update_data["version"] = version
    if tags is not None:
        update_data["tags"] = tags

    if update_data:
        update_apk(apk_id, **update_data)

    return get_apk(apk_id)


@router.delete("/{apk_id}")
async def api_delete_apk(apk_id: int):
    """删除APK"""
    apk = get_apk(apk_id)
    if not apk:
        raise HTTPException(status_code=404, detail="APK不存在")

    if os.path.exists(apk.get("file_path", "")):
        os.remove(apk["file_path"])

    delete_apk(apk_id)
    return {"success": True}


@router.post("/batch-delete")
async def api_batch_delete_apks(data: dict):
    """批量删除APK"""
    ids = data.get("ids", [])
    deleted = 0
    for apk_id in ids:
        apk = get_apk(apk_id)
        if apk:
            if os.path.exists(apk.get("file_path", "")):
                os.remove(apk["file_path"])
            delete_apk(apk_id)
            deleted += 1

    return {"success": True, "deleted_count": deleted}


class InstallApkRequest(BaseModel):
    """APK 安装请求体（与前端 confirmInstall 发送字段对齐）"""

    device_ids: List[str]
    uninstall_first: bool = False
    package_name: Optional[str] = None


@router.post("/{apk_id}/install")
async def api_install_apk(apk_id: int, body: InstallApkRequest):
    """安装APK到设备（支持多设备并发安装）"""
    apk = get_apk(apk_id)
    if not apk:
        raise HTTPException(status_code=404, detail="APK不存在")

    if not os.path.exists(apk.get("file_path", "")):
        raise HTTPException(status_code=400, detail="APK文件不存在")

    if not body.device_ids:
        raise HTTPException(status_code=400, detail="未选择安装设备")

    file_path = apk["file_path"]

    def _install_one(dev_id: str) -> Dict[str, Any]:
        # 先卸载再安装：卸载失败（如未安装）不阻断后续安装
        if body.uninstall_first and body.package_name:
            mcp_tools.uninstall_apk(body.package_name, dev_id)
        return mcp_tools.install_apk(file_path, dev_id)

    # 并发安装到多设备，每台都通过 device_id 构造带 -s 的 ADBMCTools
    results = await asyncio.gather(
        *(asyncio.to_thread(_install_one, dev) for dev in body.device_ids)
    )

    success_count = sum(1 for r in results if r.get("success"))
    failed = [
        (dev, r) for dev, r in zip(body.device_ids, results) if not r.get("success")
    ]

    if not failed:
        return {"success": True, "success_count": success_count, "message": "安装成功"}

    fail_messages = "; ".join(f"[{dev}] {r.get('message', '')}" for dev, r in failed)
    return {
        "success": success_count > 0,
        "success_count": success_count,
        "message": fail_messages,
    }
