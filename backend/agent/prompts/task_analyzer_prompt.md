{core_capabilities}

请分析以下任务是否属于我的能力范围：

任务：{task}

请以JSON格式返回分析结果，格式如下：
{{
    "can_handle": true/false,
    "reason": "原因说明",
    "suggestion": "如果不能处理，给出建议",
    "required_app": "需要使用的应用名称（如微信、抖音等），如果没有则为null",
    "task_type": "任务类型（如：应用操作、设备操作、内容创作、知识问答等）"
}}

注意：
- 只返回JSON，不要其他文字
- 如果任务涉及特定应用（如微信、抖音、淘宝等），在required_app中注明
- 如果任务超出能力范围，给出友好的建议（如建议在手机上打开相关应用）
