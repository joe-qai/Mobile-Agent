"""产物存储 - 保存截图等文件并写入task_artifacts"""
import os
from datetime import datetime
from typing import Dict, Optional

from backend.db.database import execute_update


class ArtifactStore:
    """产物存储管理器"""
    
    def __init__(self, base_path: str = None):
        self.base_path = base_path or os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "artifacts", "compat"
        )
        os.makedirs(self.base_path, exist_ok=True)
    
    def get_task_dir(self, parent_task_id: int, child_task_id: int) -> str:
        """获取任务产物目录"""
        task_dir = os.path.join(self.base_path, str(parent_task_id), str(child_task_id))
        os.makedirs(task_dir, exist_ok=True)
        return task_dir
    
    def get_relative_path(self, parent_task_id: int, child_task_id: int, filename: str) -> str:
        """获取相对路径"""
        return f"compat/{parent_task_id}/{child_task_id}/{filename}"
    
    def save_screenshot(
        self,
        parent_task_id: int,
        child_task_id: int,
        screenshot_bytes: bytes,
        step_index: int = None,
        assertion_name: str = None,
        kind: str = "after_action",
    ) -> str:
        """
        保存截图
        
        Args:
            parent_task_id: 父任务ID
            child_task_id: 子任务ID
            screenshot_bytes: 截图字节数据
            step_index: 步骤索引
            assertion_name: 断言名称
            kind: 截图类型
        
        Returns:
            相对路径
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 生成文件名
        parts = ["screenshot"]
        if step_index is not None:
            parts.append(f"step{step_index}")
        if assertion_name:
            # 清理断言名称作为文件名
            safe_name = "".join(c for c in assertion_name if c.isalnum() or c in "_-")
            parts.append(safe_name[:50])
        parts.append(timestamp)
        parts.append("png")
        
        filename = "_".join(parts)
        task_dir = self.get_task_dir(parent_task_id, child_task_id)
        file_path = os.path.join(task_dir, filename)
        
        with open(file_path, "wb") as f:
            f.write(screenshot_bytes)
        
        # 记录到数据库
        self._record_artifact(
            task_id=child_task_id,
            parent_task_id=parent_task_id,
            artifact_type="screenshot",
            relative_path=self.get_relative_path(parent_task_id, child_task_id, filename),
            step_index=step_index,
            assertion_name=assertion_name,
        )
        
        return self.get_relative_path(parent_task_id, child_task_id, filename)
    
    def save_log(self, parent_task_id: int, child_task_id: int, log_content: str) -> str:
        """
        保存日志文件
        
        Args:
            parent_task_id: 父任务ID
            child_task_id: 子任务ID
            log_content: 日志内容
        
        Returns:
            相对路径
        """
        filename = f"execution_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        task_dir = self.get_task_dir(parent_task_id, child_task_id)
        file_path = os.path.join(task_dir, filename)
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(log_content)
        
        self._record_artifact(
            task_id=child_task_id,
            parent_task_id=parent_task_id,
            artifact_type="log",
            relative_path=self.get_relative_path(parent_task_id, child_task_id, filename),
        )
        
        return self.get_relative_path(parent_task_id, child_task_id, filename)
    
    def save_report(
        self,
        parent_task_id: int,
        child_task_id: Optional[int],
        report_content: str,
        artifact_type: str = "report",
    ) -> str:
        """
        保存报告文件
        
        Args:
            parent_task_id: 父任务ID
            child_task_id: 子任务ID（None表示父任务报告）
            report_content: 报告内容
        
        Returns:
            相对路径
        """
        if child_task_id:
            filename = f"report_{child_task_id}.html"
            task_dir = self.get_task_dir(parent_task_id, child_task_id)
        else:
            filename = "report.html"
            task_dir = os.path.join(self.base_path, str(parent_task_id))
            os.makedirs(task_dir, exist_ok=True)
        
        file_path = os.path.join(task_dir, filename)
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(report_content)
        
        relative_path = f"compat/{parent_task_id}/{filename}" if not child_task_id else \
            self.get_relative_path(parent_task_id, child_task_id, filename)
        
        self._record_artifact(
            task_id=child_task_id if child_task_id is not None else parent_task_id,
            parent_task_id=parent_task_id,
            artifact_type=artifact_type,
            relative_path=relative_path,
        )
        
        return relative_path
    
    def _record_artifact(
        self,
        task_id: int,
        parent_task_id: int,
        artifact_type: str,
        relative_path: str,
        step_index: int = None,
        assertion_name: str = None,
    ):
        """
        记录产物到数据库
        
        Args:
            task_id: 子任务ID
            parent_task_id: 父任务ID
            artifact_type: 产物类型
            relative_path: 相对路径
            step_index: 步骤索引
            assertion_name: 断言名称
        """
        query = """
            INSERT INTO task_artifacts (
                task_id, parent_task_id, artifact_type, relative_path,
                step_index, assertion_name, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """
        execute_update(query, (task_id, parent_task_id, artifact_type, relative_path, step_index, assertion_name))
    
    def get_artifact_path(self, relative_path: str) -> str:
        """
        根据相对路径获取绝对路径
        
        Args:
            relative_path: 相对路径
        
        Returns:
            绝对路径
        """
        return os.path.join(os.path.dirname(self.base_path), relative_path)
    
    def artifact_exists(self, relative_path: str) -> bool:
        """
        检查产物是否存在
        
        Args:
            relative_path: 相对路径
        
        Returns:
            True if exists, False otherwise
        """
        return os.path.exists(self.get_artifact_path(relative_path))


# 全局产物存储实例
artifact_store = ArtifactStore()
