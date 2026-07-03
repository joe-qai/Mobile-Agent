"""报告生成器 - 生成父任务/子任务HTML报告"""

import json
from typing import Any, Dict, List

from .artifact_store import artifact_store
from .assertions import DIMENSION_DESCRIPTIONS


class ReportBuilder:
    """报告生成器 - 支持设备上下文展示和VLM分析结果可视化"""

    PARENT_REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f7fa; min-height: 100vh; padding: 20px; }}
        .report-container {{ max-width: 1200px; margin: 0 auto; }}
        .report-header {{ background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); padding: 30px; border-radius: 16px; color: white; margin-bottom: 20px; }}
        .report-header h1 {{ font-size: 24px; margin-bottom: 8px; }}
        .report-header p {{ opacity: 0.8; font-size: 14px; }}
        .status-badge {{ display: inline-flex; align-items: center; gap: 6px; padding: 6px 16px; border-radius: 20px; font-size: 14px; font-weight: 500; margin-top: 16px; }}
        .status-finished {{ background: #d4edda; color: #155724; }}
        .status-failed {{ background: #f8d7da; color: #721c24; }}
        .status-partial_failed {{ background: #fff3cd; color: #856404; }}
        .summary-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 16px; margin-bottom: 20px; }}
        .summary-card {{ background: white; padding: 20px; border-radius: 12px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
        .summary-card .value {{ font-size: 32px; font-weight: 700; margin-bottom: 4px; }}
        .summary-card.pass {{ color: #10b981; }}
        .summary-card.fail {{ color: #ef4444; }}
        .summary-card.total {{ color: #667eea; }}
        .summary-card.device {{ color: #f59e0b; }}
        .summary-card.warning {{ color: #f59e0b; }}
        .summary-card .label {{ font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }}
        .summary-section {{ background: white; border-radius: 12px; margin-bottom: 20px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
        .summary-section h2 {{ font-size: 16px; color: #333; margin-bottom: 16px; }}
        .dimension-section {{ background: white; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); overflow: hidden; }}
        .dimension-header {{ display: flex; justify-content: space-between; align-items: center; padding: 20px; cursor: pointer; user-select: none; transition: background 0.15s; }}
        .dimension-header:hover {{ background: #f8f9fa; }}
        .dimension-header h3 {{ font-size: 16px; color: #333; margin: 0; }}
        .dimension-header .toggle-icon {{ font-size: 14px; color: #999; transition: transform 0.2s; }}
        .dimension-section.collapsed .dimension-header .toggle-icon {{ transform: rotate(-90deg); }}
        .dimension-section.collapsed .dimension-body {{ display: none; }}
        .dimension-body {{ padding: 0 20px 20px; }}
        .dimension-table {{ width: 100%; border-collapse: collapse; }}
        .dimension-table th, .dimension-table td {{ padding: 12px; text-align: left; border-bottom: 1px solid #eee; }}
        .dimension-table th {{ background: #f8f9fa; font-weight: 600; font-size: 13px; color: #666; }}
        .dimension-table td {{ font-size: 14px; }}
        .pass-rate {{ font-weight: 600; }}
        .pass-rate.high {{ color: #10b981; }}
        .pass-rate.medium {{ color: #f59e0b; }}
        .pass-rate.low {{ color: #ef4444; }}
        .device-section {{ background: white; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); overflow: hidden; }}
        .device-header {{ display: flex; justify-content: space-between; align-items: center; padding: 20px; cursor: pointer; user-select: none; transition: background 0.15s; }}
        .device-header:hover {{ background: #f8f9fa; }}
        .device-header-left {{ flex: 1; }}
        .device-header-left h3 {{ font-size: 16px; color: #333; margin-bottom: 8px; }}
        .device-header-right {{ display: flex; align-items: center; gap: 12px; }}
        .device-status {{ padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 500; }}
        .toggle-icon {{ font-size: 14px; color: #999; transition: transform 0.2s; }}
        .device-section.collapsed .toggle-icon {{ transform: rotate(-90deg); }}
        .device-section.collapsed .device-body {{ display: none; }}
        .device-body {{ padding: 0 20px 20px; }}
        .device-status.finished {{ background: #d4edda; color: #155724; }}
        .device-status.failed {{ background: #f8d7da; color: #721c24; }}
        .device-status.warning {{ background: #fff3cd; color: #856404; }}
        .device-context {{ background: #f8f9fa; padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; }}
        .device-context .context-row {{ display: flex; flex-wrap: wrap; gap: 16px; }}
        .device-context .context-item {{ font-size: 13px; }}
        .device-context .context-label {{ color: #666; margin-right: 4px; }}
        .device-context .context-value {{ color: #333; font-weight: 500; }}
        .assertion-list {{ list-style: none; }}
        .assertion-item {{ padding: 16px; border-radius: 12px; margin-bottom: 12px; border-left: 4px solid; }}
        .assertion-item.severity-blocker {{ background: #fff5f5; border-color: #ef4444; }}
        .assertion-item.severity-major {{ background: #fffbeb; border-color: #f59e0b; }}
        .assertion-item.severity-minor {{ background: #fefce8; border-color: #eab308; }}
        .assertion-item.severity-suggestion {{ background: #eff6ff; border-color: #3b82f6; }}
        .assertion-item.pending_review {{ background: #e8f4fd; border-color: #3b82f6; }}
        .assessment-pending_review {{ background: #dbeafe; color: #1e40af; }}
        .assertion-title-row {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; }}
        .assertion-item .name {{ font-weight: 600; font-size: 14px; }}
        .baseline-badge {{ display: inline-flex; align-items: center; margin-left: 8px; padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; letter-spacing: .2px; }}
        .baseline-badge-new {{ background: #ff6b6b; color: white; box-shadow: 0 0 0 3px rgba(255,107,107,.12); }}
        .baseline-badge-reused {{ background: #e0f2fe; color: #0369a1; }}
        .baseline-actions {{ display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }}
        .baseline-actions button {{ border: 1px solid #e5e7eb; background: #fff; color: #374151; border-radius: 6px; padding: 3px 8px; font-size: 11px; cursor: pointer; }}
        .baseline-actions button:hover {{ background: #f9fafb; }}
        .baseline-actions .confirm {{ border-color: #fecaca; color: #dc2626; background: #fef2f2; }}
        .baseline-actions .reject {{ border-color: #d1d5db; color: #4b5563; }}
        .baseline-actions .skip {{ border-color: #bfdbfe; color: #2563eb; background: #eff6ff; }}
        .baseline-actions .delete {{ border-color: #fed7aa; color: #ea580c; background: #fff7ed; }}
        .assertion-item .meta {{ font-size: 12px; color: #666; margin-top: 4px; }}
        .assertion-item .message {{ font-size: 13px; color: #555; margin-top: 8px; }}
        .assertion-item .evidence {{ margin-top: 12px; }}
        .assertion-item .screenshot-container {{ margin-top: 12px; display: inline-block; position: relative; }}
        .assertion-item .screenshot-label {{ font-size: 11px; color: #999; margin-bottom: 4px; }}
        .assertion-item .screenshot-img {{ max-width: 120px; max-height: 160px; border-radius: 6px; cursor: zoom-in; box-shadow: 0 1px 4px rgba(0,0,0,0.12); transition: box-shadow 0.15s; }}
        .assertion-item .screenshot-img:hover {{ box-shadow: 0 2px 10px rgba(0,0,0,0.2); }}
        .modal-overlay {{ display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.75); z-index: 9999; cursor: pointer; }}
        .modal-overlay.active {{ display: flex; align-items: center; justify-content: center; }}
        .modal-overlay .modal-img {{ max-width: 90vw; max-height: 90vh; border-radius: 8px; box-shadow: 0 4px 24px rgba(0,0,0,0.4); cursor: default; }}
        .modal-overlay .modal-close {{ position: absolute; top: 20px; right: 28px; color: white; font-size: 32px; font-weight: 300; cursor: pointer; opacity: 0.7; transition: opacity 0.15s; font-family: sans-serif; }}
        .modal-overlay .modal-close:hover {{ opacity: 1; }}
        .timestamp {{ font-size: 12px; color: #888; margin-top: 16px; }}
        .evidence-tags {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }}
        .evidence-tag {{ padding: 2px 8px; background: #e0e7ff; color: #4338ca; border-radius: 4px; font-size: 11px; }}
        .issue-card {{ background: #fff5f5; border-radius: 8px; padding: 12px; margin-bottom: 8px; }}
        .issue-card.warning {{ background: #fffbeb; }}
        .issue-card.info {{ background: #eff6ff; }}
        .issue-card.major {{ border-left: 3px solid #ef4444; }}
        .issue-card.minor {{ border-left: 3px solid #f59e0b; }}
        .issue-card.suggestion {{ border-left: 3px solid #3b82f6; }}
        .issue-card .issue-category {{ font-size: 11px; color: #666; margin-bottom: 4px; }}
        .issue-card .issue-title {{ font-weight: 500; font-size: 13px; color: #333; margin-bottom: 4px; }}
        .issue-card .issue-location {{ font-size: 11px; color: #888; }}
        .issue-card .issue-suggestion {{ font-size: 12px; color: #059669; margin-top: 4px; }}
        .vlm-analysis-section {{ margin-top: 16px; padding-top: 16px; border-top: 1px solid #eee; }}
        .vlm-analysis-section h4 {{ font-size: 14px; margin-bottom: 12px; color: #333; }}
        .assessment-badge {{ padding: 4px 12px; border-radius: 8px; font-size: 12px; font-weight: 500; }}
        .assessment-blocker {{ background: #fee2e2; color: #991b1b; }}
        .assessment-major {{ background: #ffedd5; color: #9a3412; }}
        .assessment-minor {{ background: #fef3c7; color: #92400e; }}
        .assessment-suggestion {{ background: #dcfce7; color: #166534; }}
        .confidence-bar {{ height: 6px; background: #eee; border-radius: 3px; overflow: hidden; margin-top: 8px; }}
        .confidence-bar-fill {{ height: 100%; border-radius: 3px; transition: width 0.3s; }}
        .confidence-high {{ background: #10b981; }}
        .confidence-medium {{ background: #f59e0b; }}
        .confidence-low {{ background: #ef4444; }}
        .device-summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }}
        .device-summary-card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px; }}
        .device-summary-card h3 {{ font-size: 15px; color: #333; margin-bottom: 8px; }}
        .device-summary-card ul {{ padding-left: 18px; color: #374151; font-size: 13px; }}
        .device-summary-card li + li {{ margin-top: 6px; }}
    </style>
</head>
<body>
    <div class="report-container">
        <div class="report-header">
            <h1>{title}</h1>
            <p>兼容性测试报告 - {platform}</p>
            <span class="status-badge status-{status}">{status_label}</span>
        </div>
        
        <div class="summary-cards">
            <div class="summary-card total"><div class="value">{total_devices}</div><div class="label">设备总数</div></div>
            <div class="summary-card total"><div class="value">{check_total}</div><div class="label">检查总数</div></div>
            <div class="summary-card fail"><div class="value">{blocker_count}</div><div class="label">阻塞</div></div>
            <div class="summary-card warning"><div class="value">{major_count}</div><div class="label">严重</div></div>
            <div class="summary-card warning"><div class="value">{minor_count}</div><div class="label">警告</div></div>
            <div class="summary-card total"><div class="value">{suggestion_count}</div><div class="label">建议</div></div>
        </div>

        {device_matrix_html}

        {device_summary_cards_html}
        
        <div class="dimension-section">
            <div class="dimension-header" onclick="toggleDimension(this)">
                <h3>维度汇总</h3>
                <span class="toggle-icon">&#9660;</span>
            </div>
            <div class="dimension-body">
                <table class="dimension-table">
                    <thead><tr><th>维度</th><th>总数</th><th>阻塞</th><th>严重</th><th>警告</th><th>建议</th></tr></thead>
                    <tbody>
                        {dimension_rows}
                    </tbody>
                </table>
            </div>
        </div>
        
        {device_sections}
        
        <div class="timestamp">生成时间: {generated_at}</div>
    </div>
    <div class="modal-overlay" id="imageModal" onclick="closeModal()">
        <span class="modal-close">&times;</span>
        <img class="modal-img" id="modalImg" src="" alt="放大截图">
    </div>
    <script>
        function toggleDevice(header) {{
            var section = header.parentElement;
            section.classList.toggle('collapsed');
        }}
        function toggleDimension(header) {{
            var section = header.parentElement;
            section.classList.toggle('collapsed');
        }}
        function openModal(src) {{
            document.getElementById('modalImg').src = src;
            document.getElementById('imageModal').classList.add('active');
        }}
        function closeModal() {{
            document.getElementById('imageModal').classList.remove('active');
        }}
        document.addEventListener('keydown', function(e) {{
            if (e.key === 'Escape') closeModal();
        }});
    </script>
</body>
</html>"""

    @classmethod
    def _normalize_evidence(cls, evidence: Any) -> Dict[str, Any]:
        """Normalize DB JSON evidence and in-memory evidence to a dict."""
        if isinstance(evidence, dict):
            return evidence
        if isinstance(evidence, str) and evidence.strip():
            from .event_parser import safe_json_parse

            parsed = safe_json_parse(evidence)
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @classmethod
    def generate_parent_report(
        cls,
        parent_task_id: int,
        title: str,
        platform: str,
        status: str,
        summary: Dict[str, Any],
        device_results: List[Dict[str, Any]],
    ) -> str:
        """
        生成父任务报告

        Args:
            parent_task_id: 父任务ID
            title: 报告标题
            platform: 平台
            status: 状态
            summary: 汇总数据
            device_results: 各设备结果

        Returns:
            HTML报告内容
        """
        # 状态标签
        status_labels = {
            "finished": "已完成",
            "failed": "阻塞",
            "partial_failed": "部分异常",
            "cancelled": "已取消",
        }

        # 统计警告和严重问题
        warning_count = 0
        blocker_count = 0
        for device_result in device_results:
            for assertion in device_result.get("assertions", []):
                severity = assertion.get("severity", "")
                if severity == "blocker":
                    blocker_count += 1
                elif severity in ["major", "warning"]:
                    warning_count += 1

        # 维度行
        dimension_rows = ""
        for dim_code, dim_data in summary.get("by_dimension", {}).items():
            dimension_rows += f"""
            <tr>
                <td>{DIMENSION_DESCRIPTIONS.get(dim_code, dim_code)}</td>
                <td>{dim_data.get("total", 0)}</td>
                <td>{dim_data.get("blocker", 0)}</td>
                <td>{dim_data.get("major", 0)}</td>
                <td>{dim_data.get("minor", 0)}</td>
                <td>{dim_data.get("suggestion", 0)}</td>
            </tr>"""

        # 设备部分
        device_sections = ""
        for device_result in device_results:
            device_sections += cls._generate_device_section(device_result)
        device_matrix_html = cls._generate_device_matrix_html(
            parent_task_id, device_results
        )
        device_summary_cards_html = cls._generate_device_summary_cards_html(
            device_results
        )

        import datetime

        generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return cls.PARENT_REPORT_TEMPLATE.format(
            title=title,
            platform=platform,
            status=status,
            status_label=status_labels.get(status, status),
            total_devices=summary.get("total_devices", 0),
            check_total=summary.get("check_total", 0),
            blocker_count=summary.get("blocker_count", 0),
            major_count=summary.get("major_count", 0),
            minor_count=summary.get("minor_count", 0),
            suggestion_count=summary.get("suggestion_count", 0),
            device_matrix_html=device_matrix_html,
            device_summary_cards_html=device_summary_cards_html,
            dimension_rows=dimension_rows,
            device_sections=device_sections,
            generated_at=generated_at,
        )

    @classmethod
    def _get_device_key_issues(
        cls, assertions: List[Dict[str, Any]], limit: int = 3
    ) -> List[Dict[str, str]]:
        severity_rank = {
            "blocker": 0,
            "major": 1,
            "minor": 2,
            "warning": 2,
            "suggestion": 3,
        }
        issues = []
        for assertion in assertions:
            evidence = cls._normalize_evidence(assertion.get("evidence", {}))
            vlm_analysis = (
                evidence.get("vlm_analysis", {}) if isinstance(evidence, dict) else {}
            )
            vlm_issues = (
                vlm_analysis.get("issues", []) if isinstance(vlm_analysis, dict) else []
            )
            if not (isinstance(vlm_issues, list) and vlm_issues):
                issues.append(
                    {
                        "severity": str(
                            assertion.get("severity", "suggestion")
                        ).lower(),
                        "category": assertion.get("dimension")
                        or assertion.get("name", ""),
                        "description": assertion.get("message")
                        or assertion.get("name", ""),
                    }
                )
                continue
            for issue in vlm_issues if isinstance(vlm_issues, list) else []:
                if not isinstance(issue, dict):
                    continue
                issues.append(
                    {
                        "severity": issue.get(
                            "severity", assertion.get("severity", "suggestion")
                        ),
                        "category": issue.get(
                            "category", assertion.get("dimension", "")
                        ),
                        "description": issue.get(
                            "description", assertion.get("message", "")
                        ),
                    }
                )
        issues.sort(
            key=lambda item: severity_rank.get(item.get("severity", "suggestion"), 99)
        )
        return issues[:limit]

    @classmethod
    def _get_device_worst_severity(cls, assertions: List[Dict[str, Any]]) -> str:
        severity_rank = {
            "blocker": 0,
            "major": 1,
            "minor": 2,
            "warning": 2,
            "suggestion": 3,
        }
        worst = ""
        for issue in cls._get_device_key_issues(assertions, limit=100):
            severity = issue.get("severity", "")
            if not worst or severity_rank.get(severity, 99) < severity_rank.get(
                worst, 99
            ):
                worst = severity
        if worst:
            return worst
        for assertion in assertions:
            severity = str(assertion.get("severity", "")).lower()
            if not worst or severity_rank.get(severity, 99) < severity_rank.get(
                worst, 99
            ):
                worst = severity
        return worst or "suggestion"

    # 平台名归一映射（DB 存储可能是 "android"/"Android"，统一展示为 "Android"）。
    _PLATFORM_LABELS = {
        "android": "Android",
        "harmonyos": "HarmonyOS",
        "harmony": "HarmonyOS",
        "ios": "iOS",
    }

    @classmethod
    def _format_platform_label(cls, raw_platform: Any) -> str:
        """把 device_info 中的 platform 字段归一为展示用名；未识别返回空串。

        平台名由调用方按需拼到系统版本前；未识别平台不补名，避免在历史数据上臆造前缀。
        """
        text = str(raw_platform or "").strip()
        if not text:
            return ""
        return cls._PLATFORM_LABELS.get(text.lower(), "")

    @classmethod
    def _get_device_os_version(cls, device_info: Dict[str, Any]) -> str:
        platform_label = cls._format_platform_label(device_info.get("platform"))
        for key in (
            "os_version",
            "system_version",
            "android_version",
            "ios_version",
            "version",
        ):
            value = str(device_info.get(key) or "").strip()
            if not value or value.lower() == "unknown":
                continue
            # 避免 "Android Android 9" / "iOS iOS 16" 重复拼接，
            # 并把已带前缀但大小写不规范的版本号（如 "harmonyos 3.0"）归一。
            if platform_label:
                prefix_len = len(platform_label)
                if (
                    len(value) >= prefix_len
                    and value[:prefix_len].lower() == platform_label.lower()
                ):
                    rest = value[prefix_len:].lstrip()
                    return f"{platform_label} {rest}" if rest else platform_label
                return f"{platform_label} {value}"
            return value
        return "未知"

    @classmethod
    def _child_report_href(cls, parent_task_id: int, report_path: str) -> str:
        """将子报告相对路径归一为"以 compat/ 开头、不带前导斜杠"的相对链接。

        父报告 HTML 主要用于"下载到本地"的场景：zip 根目录是主报告
        ``report_<parent>.html``，子报告位于 ``compat/<parent>/<child>/report_<child>.html``，
        因此主报告内必须使用从 zip 根目录出发的相对链接，本地双击 HTML 才能正确跳转。
        父报告 HTML 落盘到 ``data/artifacts`` 后，亦可经 FastAPI 静态目录或
        ``/api/reports/{id}/html`` 路由提供；web 上下文在读出 HTML 后由路由做
        ``compat/`` → ``/artifacts/compat/`` 的后处理，从而在 web 上仍可点开。
        """
        if not report_path:
            return ""
        normalized = report_path.lstrip("/")
        if normalized.startswith("compat/"):
            return normalized
        if "/" in normalized:
            # 历史数据可能使用了不同的相对前缀：直接当作相对路径返回。
            return normalized
        return normalized

    @classmethod
    def _script_source_label(cls, evidence: Dict[str, Any]) -> str:
        script_name = str(evidence.get("script_name") or "").strip()
        if script_name:
            return script_name
        script_id = evidence.get("script_id")
        if script_id not in (None, ""):
            return f"脚本ID {script_id}"
        script_index = evidence.get("script_index")
        if script_index not in (None, ""):
            try:
                return f"脚本 #{int(script_index) + 1}"
            except (TypeError, ValueError):
                return f"脚本 #{script_index}"
        return ""

    @classmethod
    def _generate_device_matrix_html(
        cls, parent_task_id: int, device_results: List[Dict[str, Any]]
    ) -> str:
        rows = ""
        for device_result in device_results:
            device_info = device_result.get("device_info") or {}
            model = cls._format_brand_model(device_info) or device_result.get(
                "device_id", "unknown"
            )
            os_version = cls._get_device_os_version(device_info)
            assertions = device_result.get("assertions", [])
            worst_severity = cls._get_device_worst_severity(assertions)
            report_path = device_result.get("report_path") or ""
            if report_path:
                link_html = f'<a href="{cls._child_report_href(device_result.get("parent_task_id") or parent_task_id, report_path)}" target="_blank">查看完整报告</a>'
            else:
                link_html = "未生成"
            rows += f"""
            <tr>
                <td>{model}</td>
                <td>{os_version}</td>
                <td>{cls._get_status_label(device_result.get("status", "unknown"))}</td>
                <td>{cls._get_severity_label(worst_severity)}</td>
                <td>{len(assertions)}</td>
                <td>{link_html}</td>
            </tr>"""
        return f"""
        <section class="summary-section">
            <h2>设备状态矩阵</h2>
            <table class="dimension-table">
                <thead>
                    <tr><th>设备</th><th>系统版本</th><th>状态</th><th>最高级别</th><th>断言数</th><th>报告</th></tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
        </section>"""

    @classmethod
    def _generate_device_summary_cards_html(
        cls, device_results: List[Dict[str, Any]]
    ) -> str:
        cards = ""
        for device_result in device_results:
            device_info = device_result.get("device_info") or {}
            model = cls._format_brand_model(device_info) or device_result.get(
                "device_id", "unknown"
            )
            issues = cls._get_device_key_issues(device_result.get("assertions", []))
            if issues:
                issue_items = "".join(
                    f"<li>[{cls._get_severity_label(issue['severity'])}] {issue['category']}: {issue['description']}</li>"
                    for issue in issues
                )
            else:
                issue_items = "<li>未发现关键问题</li>"
            cards += f"""
            <div class="device-summary-card">
                <h3>{model}</h3>
                <ul>{issue_items}</ul>
            </div>"""
        return f"""
        <section class="summary-section">
            <h2>设备关键问题摘要</h2>
            <div class="device-summary-grid">{cards}</div>
        </section>"""

    @classmethod
    def _generate_device_section(cls, device_result: Dict[str, Any]) -> str:
        """
        生成设备部分HTML

        Args:
            device_result: 设备结果

        Returns:
            HTML字符串
        """
        device_status = device_result.get("status", "unknown")
        device_assertions = device_result.get("assertions", [])
        device_info = device_result.get("device_info", {})

        # 设备上下文HTML
        context_html = cls._generate_device_context_html(device_info)

        # 断言HTML
        assertions_html = ""
        for assertion in device_assertions:
            assertions_html += cls._generate_assertion_html(assertion)

        # 设备状态样式仅表示执行完成状态；问题级别由最高级别/断言卡片表达。
        status_class = device_status

        return f"""
        <div class="device-section collapsed">
            <div class="device-header" onclick="toggleDevice(this)">
                <div class="device-header-left">
                    <h3>设备: {device_result.get("device_id", "unknown")}</h3>
                    {context_html}
                </div>
                <div class="device-header-right">
                    <span class="device-status {status_class}">{cls._get_status_label(device_status)}</span>
                    <span class="toggle-icon">&#9660;</span>
                </div>
            </div>
            <div class="device-body">
                <ul class="assertion-list">
                    {assertions_html}
                </ul>
            </div>
        </div>"""

    @classmethod
    def _format_brand_model(cls, device_info: Dict[str, Any]) -> str:
        brand = str(device_info.get("brand") or "").strip()
        model = str(
            device_info.get("model") or device_info.get("device_model") or ""
        ).strip()
        return "/".join(part for part in (brand, model) if part)

    @classmethod
    def _generate_device_context_html(cls, device_info: Dict[str, Any]) -> str:
        """
        生成设备上下文HTML

        Args:
            device_info: 设备信息

        Returns:
            HTML字符串
        """
        if not device_info:
            return ""

        items = []
        if device_info.get("platform"):
            items.append(
                f"<span class='context-item'><span class='context-label'>平台:</span><span class='context-value'>{device_info['platform']}</span></span>"
            )
        brand_model = cls._format_brand_model(device_info)
        if brand_model:
            items.append(
                f"<span class='context-item'><span class='context-label'>厂商/型号</span><span class='context-value'>{brand_model}</span></span>"
            )
        os_version = cls._get_device_os_version(device_info)
        items.append(
            f"<span class='context-item'><span class='context-label'>系统版本:</span><span class='context-value'>{os_version}</span></span>"
        )
        if device_info.get("resolution"):
            items.append(
                f"<span class='context-item'><span class='context-label'>分辨率:</span><span class='context-value'>{device_info['resolution']}</span></span>"
            )
        if device_info.get("theme"):
            items.append(
                f"<span class='context-item'><span class='context-label'>主题:</span><span class='context-value'>{device_info['theme']}</span></span>"
            )

        return f"""
        <div class="device-context">
            <div class="context-row">
                {"".join(items)}
            </div>
        </div>"""

    @classmethod
    def _generate_assertion_html(cls, assertion: Dict[str, Any]) -> str:
        """
        生成断言HTML

        Args:
            assertion: 断言信息

        Returns:
            HTML字符串
        """
        status = assertion.get("status", "passed")
        severity = assertion.get("severity", "medium")

        # 处理截图（优先使用标注后的截图，其次是原始截图）
        screenshot_html = ""
        evidence = cls._normalize_evidence(assertion.get("evidence", {}))
        provenance_html = cls._generate_baseline_provenance_html(evidence)
        if evidence:
            base64_data = evidence.get("annotated_screenshot_base64") or evidence.get(
                "screenshot_base64"
            )
            if base64_data:
                screenshot_src = f"data:image/png;base64,{base64_data}"
                screenshot_html = f"""
            <div class="screenshot-container">
                <div class="screenshot-label">截图证据</div>
                <img src="{screenshot_src}" class="screenshot-img" alt="截图" loading="lazy" onclick="openModal(this.src)">
            </div>"""
            elif evidence.get("screenshot"):
                screenshot_path = evidence["screenshot"]
                screenshot_html = f"""
            <div class="screenshot-container">
                <div class="screenshot-label">截图证据</div>
                <img src="{screenshot_path}" class="screenshot-img" alt="截图" loading="lazy" onclick="openModal(this.src)">
            </div>"""

        # 处理VLM分析问题
        vlm_html = cls._generate_vlm_analysis_html(evidence)
        baseline_badges_html = cls._generate_baseline_badges_html(evidence)
        baseline_actions_html = cls._generate_baseline_review_actions_html(evidence)
        # Review buttons only appear alongside screenshot evidence; suppress when no screenshot
        final_actions_html = baseline_actions_html if screenshot_html else ""
        script_source = cls._script_source_label(evidence)
        script_meta = f" | 脚本: {script_source}" if script_source else ""

        # 确定断言样式类（由 severity 驱动）
        if status == "pending_review":
            item_class = "pending_review"
        elif severity in ("blocker", "major", "minor", "suggestion"):
            item_class = f"severity-{severity}"
        else:
            item_class = status

        return f"""
        <li class="assertion-item {item_class}">
            <div class="assertion-title-row">
                <div class="name">{assertion.get("name", "未命名断言")}{baseline_badges_html}</div>
            </div>
            <div class="meta">维度: {DIMENSION_DESCRIPTIONS.get(assertion.get("dimension"), assertion.get("dimension", "unknown"))} | 严重程度: {cls._get_severity_label(severity)}{script_meta}</div>
            {assertion.get("message", "") and f'<div class="message">{assertion["message"]}</div>' or ""}
            {vlm_html}
            {screenshot_html}
            {final_actions_html}
        </li>"""

    @classmethod
    def _generate_baseline_provenance_html(cls, evidence: Dict[str, Any]) -> str:
        """Render whether VLM analysis is new or reused from a reviewed baseline."""
        return cls._generate_baseline_badges_html(evidence)

    @classmethod
    def _generate_baseline_badges_html(cls, evidence: Dict[str, Any]) -> str:
        """Render baseline provenance badges for the assertion title row."""
        state = evidence.get("analysis_state")
        if state == "new":
            return '<span class="baseline-badge baseline-badge-new">New</span>'
        if state == "reused":
            status = evidence.get("baseline_review_status", "")
            remark = evidence.get("baseline_review_remark", "")
            parts = ['<span class="baseline-badge baseline-badge-reused">Reused</span>']
            if status:
                parts.append(
                    f'<span class="baseline-badge baseline-badge-reused">{status}</span>'
                )
            if remark:
                parts.append(
                    f'<span class="baseline-badge baseline-badge-reused">{remark}</span>'
                )
            return "".join(parts)
        return ""

    @classmethod
    def _generate_baseline_review_actions_html(cls, evidence: Dict[str, Any]) -> str:
        """Render review actions for a pending screenshot analysis baseline."""
        return ""

    @classmethod
    def _get_checkpoint_label(cls, issues: List[Dict[str, Any]]) -> tuple[str, str]:
        """Return the label/color for the highest severity in a checkpoint."""
        severity_order = ("blocker", "major", "minor", "warning", "suggestion")
        labels = {
            "blocker": ("阻塞", "#e74c3c"),
            "major": ("严重", "#e67e22"),
            "minor": ("警告", "#f1c40f"),
            "warning": ("警告", "#f1c40f"),
            "suggestion": ("建议", "#3498db"),
        }
        normalized = [
            str(issue.get("severity") or "").lower()
            for issue in issues or []
            if isinstance(issue, dict)
        ]
        for severity in severity_order:
            if severity in normalized:
                return labels[severity]
        return "通过", "#2ecc71"

    @classmethod
    def _get_analysis_severity(cls, vlm_result: Dict[str, Any]) -> str:
        """Map VLM assessment/issues to blocker/major/minor/suggestion display severity."""
        order = {"blocker": 4, "major": 3, "minor": 2, "suggestion": 1}
        worst = ""
        issues = vlm_result.get("issues", [])
        for issue in issues if isinstance(issues, list) else []:
            if not isinstance(issue, dict):
                continue
            severity = str(issue.get("severity", "")).lower()
            if severity in order and (not worst or order[severity] > order[worst]):
                worst = severity
        if worst:
            return worst
        assessment = str(vlm_result.get("overall_assessment", "")).lower()
        return {
            "fail": "blocker",
            "failed": "blocker",
            "warning": "minor",
            "warn": "minor",
            "pass": "suggestion",
            "passed": "suggestion",
        }.get(assessment, "suggestion")

    @classmethod
    def _generate_vlm_analysis_html(cls, evidence: Dict[str, Any]) -> str:
        """
        生成VLM分析结果HTML

        Args:
            evidence: 证据信息

        Returns:
            HTML字符串
        """
        if not evidence or not evidence.get("vlm_analysis"):
            return ""

        vlm_result = evidence["vlm_analysis"]

        # VLM 请求超时或 JSON 解析失败 → 显示需人工审核
        if vlm_result.get("needs_manual_review") and vlm_result.get("error"):
            error_msg = vlm_result.get("error", "")
            is_timeout_or_parse = (
                "超时" in error_msg
                or "timeout" in error_msg.lower()
                or "无法解析 JSON" in error_msg
                or "JSON 解析失败" in error_msg
                or "HTTP错误" in error_msg
                or "空内容" in error_msg
            )
            if is_timeout_or_parse:
                return """
        <div class="vlm-analysis-section">
            <h4>VLM UI兼容性分析</h4>
            <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px;">
                <span class="assessment-badge assessment-pending_review">需人工审核</span>
            <span style="font-size: 12px; color: #666;">VLM 分析异常，请人工复核</span>
            </div>
        </div>"""

        # 总体评估
        confidence = vlm_result.get("confidence", 0.0)
        issues = vlm_result.get("issues", [])
        analysis_severity = cls._get_analysis_severity(vlm_result)

        # 置信度样式
        conf_class = (
            "confidence-high"
            if confidence >= 0.7
            else ("confidence-medium" if confidence >= 0.4 else "confidence-low")
        )
        assess_class = f"assessment-{analysis_severity}"

        issues_html = ""
        for issue in issues:
            issue_severity = issue.get("severity", "minor")
            severity_class = "major" if issue_severity == "blocker" else issue_severity
            issues_html += f"""
            <div class="issue-card {severity_class}">
                <div class="issue-category">{issue.get("category", "unknown")} - {cls._get_severity_label(issue_severity)}</div>
                <div class="issue-title">{issue.get("description", "")}</div>
                {issue.get("location") and f'<div class="issue-location">位置: {issue["location"]}</div>' or ""}
                {issue.get("suggestion") and f'<div class="issue-suggestion">建议: {issue["suggestion"]}</div>' or ""}
            </div>"""

        return f"""
        <div class="vlm-analysis-section">
            <h4>VLM UI兼容性分析</h4>
            <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px;">
                <span class="assessment-badge {assess_class}">{cls._get_severity_label(analysis_severity)}</span>
                <span style="font-size: 12px; color: #666;">置信度: {round(confidence * 100)}%</span>
            </div>
            <div class="confidence-bar">
                <div class="confidence-bar-fill {conf_class}" style="width: {confidence * 100}%"></div>
            </div>
            {issues_html}
        </div>"""

    @classmethod
    def _get_status_label(cls, status: str) -> str:
        """获取状态标签"""
        labels = {
            "finished": "完成",
            "failed": "阻塞",
            "warning": "警告",
            "unknown": "未知",
        }
        return labels.get(status, status)

    @classmethod
    def _get_severity_label(cls, severity: str) -> str:
        """获取严重程度标签"""
        labels = {
            "blocker": "阻塞",
            "major": "严重",
            "minor": "警告",
            "suggestion": "建议",
            "info": "信息",
            "high": "高",
            "medium": "中",
            "low": "低",
        }
        return labels.get(severity, severity)

    @classmethod
    def _get_assessment_label(cls, assessment: str) -> str:
        """获取评估标签"""
        labels = {
            "pass": "建议",
            "passed": "建议",
            "warning": "警告",
            "warn": "警告",
            "fail": "阻塞",
            "failed": "阻塞",
            "pending_review": "需人工审核",
        }
        return labels.get(assessment, assessment)

    @classmethod
    def generate_child_report(
        cls,
        child_task_id: int,
        parent_task_id: int,
        device_id: str,
        platform: str,
        status: str,
        assertions: List[Dict[str, Any]],
        logs: List[str] = None,
        device_info: Dict[str, Any] = None,
    ) -> str:
        """
        生成子任务报告

        Args:
            child_task_id: 子任务ID
            parent_task_id: 父任务ID
            device_id: 设备ID
            platform: 平台
            status: 状态
            assertions: 断言列表
            logs: 日志列表
            device_info: 设备信息

        Returns:
            HTML报告内容
        """
        # 设备上下文HTML
        context_html = cls._generate_device_context_html(device_info or {})

        # 断言HTML
        assertions_html = ""
        for assertion in assertions:
            assertions_html += cls._generate_assertion_html(assertion)

        # 日志HTML
        logs_html = ""
        if logs:
            for idx, log in enumerate(logs, 1):
                logs_html += f"<div style='padding: 8px; border-bottom: 1px solid #eee; font-size: 13px;'>[{idx}] {log}</div>"

        import datetime

        generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>设备 {device_id} - 兼容性测试报告</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f7fa; min-height: 100vh; padding: 20px; }}
        .report-container {{ max-width: 800px; margin: 0 auto; background: white; border-radius: 16px; padding: 30px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); }}
        .report-header {{ margin-bottom: 24px; }}
        .report-header h1 {{ font-size: 20px; margin-bottom: 8px; color: #333; }}
        .report-header p {{ color: #666; font-size: 14px; }}
        .status-badge {{ display: inline-flex; align-items: center; gap: 6px; padding: 6px 16px; border-radius: 20px; font-size: 14px; font-weight: 500; margin-top: 12px; }}
        .status-finished {{ background: #d4edda; color: #155724; }}
        .status-failed {{ background: #f8d7da; color: #721c24; }}
        .status-warning {{ background: #fff3cd; color: #856404; }}
        .section {{ margin-bottom: 24px; }}
        .section h3 {{ font-size: 16px; margin-bottom: 16px; color: #333; }}
        .assertion-list {{ list-style: none; }}
        .assertion-item {{ padding: 16px; border-radius: 12px; margin-bottom: 12px; border-left: 4px solid; }}
        .assertion-item.severity-blocker {{ background: #fff5f5; border-color: #ef4444; }}
        .assertion-item.severity-major {{ background: #fffbeb; border-color: #f59e0b; }}
        .assertion-item.severity-minor {{ background: #fefce8; border-color: #eab308; }}
        .assertion-item.severity-suggestion {{ background: #eff6ff; border-color: #3b82f6; }}
        .assertion-item.pending_review {{ background: #e8f4fd; border-color: #3b82f6; }}
        .assessment-pending_review {{ background: #dbeafe; color: #1e40af; }}
        .assertion-title-row {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; }}
        .assertion-item .name {{ font-weight: 600; font-size: 14px; }}
        .baseline-badge {{ display: inline-flex; align-items: center; margin-left: 8px; padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; letter-spacing: .2px; }}
        .baseline-badge-new {{ background: #ff6b6b; color: white; box-shadow: 0 0 0 3px rgba(255,107,107,.12); }}
        .baseline-badge-reused {{ background: #e0f2fe; color: #0369a1; }}
        .baseline-actions {{ display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }}
        .baseline-actions button {{ border: 1px solid #e5e7eb; background: #fff; color: #374151; border-radius: 6px; padding: 3px 8px; font-size: 11px; cursor: pointer; }}
        .baseline-actions button:hover {{ background: #f9fafb; }}
        .baseline-actions .confirm {{ border-color: #fecaca; color: #dc2626; background: #fef2f2; }}
        .baseline-actions .reject {{ border-color: #d1d5db; color: #4b5563; }}
        .baseline-actions .skip {{ border-color: #bfdbfe; color: #2563eb; background: #eff6ff; }}
        .baseline-actions .delete {{ border-color: #fed7aa; color: #ea580c; background: #fff7ed; }}
        .assertion-item .meta {{ font-size: 12px; color: #666; margin-top: 4px; }}
        .assertion-item .message {{ font-size: 13px; color: #555; margin-top: 8px; }}
        .assertion-item .screenshot-container {{ margin-top: 12px; }}
        .assertion-item .screenshot-label {{ font-size: 12px; color: #888; margin-bottom: 6px; }}
        .assertion-item .screenshot-img {{ max-width: 300px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
        .evidence-tags {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }}
        .evidence-tag {{ padding: 2px 8px; background: #e0e7ff; color: #4338ca; border-radius: 4px; font-size: 11px; }}
        .log-section {{ background: #f8f9fa; border-radius: 8px; padding: 16px; }}
        .timestamp {{ font-size: 12px; color: #888; margin-top: 20px; padding-top: 20px; border-top: 1px solid #eee; }}
        .device-context {{ background: #f8f9fa; padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; }}
        .device-context .context-row {{ display: flex; flex-wrap: wrap; gap: 16px; }}
        .device-context .context-item {{ font-size: 13px; }}
        .device-context .context-label {{ color: #666; margin-right: 4px; }}
        .device-context .context-value {{ color: #333; font-weight: 500; }}
        .issue-card {{ background: #fff5f5; border-radius: 8px; padding: 12px; margin-bottom: 8px; }}
        .issue-card.warning {{ background: #fffbeb; }}
        .issue-card.info {{ background: #eff6ff; }}
        .issue-card.major {{ border-left: 3px solid #ef4444; }}
        .issue-card.minor {{ border-left: 3px solid #f59e0b; }}
        .issue-card.suggestion {{ border-left: 3px solid #3b82f6; }}
        .issue-card .issue-category {{ font-size: 11px; color: #666; margin-bottom: 4px; }}
        .issue-card .issue-title {{ font-weight: 500; font-size: 13px; color: #333; margin-bottom: 4px; }}
        .issue-card .issue-location {{ font-size: 11px; color: #888; }}
        .issue-card .issue-suggestion {{ font-size: 12px; color: #059669; margin-top: 4px; }}
        .vlm-analysis-section {{ margin-top: 16px; padding-top: 16px; border-top: 1px solid #eee; }}
        .vlm-analysis-section h4 {{ font-size: 14px; margin-bottom: 12px; color: #333; }}
        .assessment-badge {{ padding: 4px 12px; border-radius: 8px; font-size: 12px; font-weight: 500; }}
        .assessment-blocker {{ background: #fee2e2; color: #991b1b; }}
        .assessment-major {{ background: #ffedd5; color: #9a3412; }}
        .assessment-minor {{ background: #fef3c7; color: #92400e; }}
        .assessment-suggestion {{ background: #dcfce7; color: #166534; }}
        .confidence-bar {{ height: 6px; background: #eee; border-radius: 3px; overflow: hidden; margin-top: 8px; }}
        .confidence-bar-fill {{ height: 100%; border-radius: 3px; transition: width 0.3s; }}
        .confidence-high {{ background: #10b981; }}
        .confidence-medium {{ background: #f59e0b; }}
        .confidence-low {{ background: #ef4444; }}
    </style>
</head>
<body>
    <div class="report-container">
        <div class="report-header">
            <h1>设备 {device_id} 兼容性测试报告</h1>
            <p>平台: {platform} | 父任务ID: {parent_task_id} | 子任务ID: {child_task_id}</p>
            {context_html}
            <span class="status-badge status-{status}">{"完成" if status == "finished" else ("警告" if status == "warning" else "阻塞")}</span>
        </div>
        
        <div class="section">
            <h3>断言结果</h3>
            <ul class="assertion-list">
                {assertions_html}
            </ul>
        </div>
        
        {logs_html and f'<div class="section"><h3>执行日志</h3><div class="log-section">{logs_html}</div></div>' or ""}
        
        <div class="timestamp">生成时间: {generated_at}</div>
    </div>
    <script>
        async function reviewCompatBaseline(id, status) {{
            await fetch('/api/compat/baselines/' + id + '/review', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{status: status, reviewed_by: 'report'}})
            }});
            location.reload();
        }}
        async function deleteCompatBaseline(id) {{
            await fetch('/api/compat/baselines/' + id, {{method: 'DELETE'}});
            location.reload();
        }}
    </script>
</body>
</html>"""

    @classmethod
    def save_parent_report(cls, parent_task_id: int, report_content: str) -> str:
        """
        保存父任务报告

        Args:
            parent_task_id: 父任务ID
            report_content: 报告内容

        Returns:
            报告相对路径
        """
        return artifact_store.save_report(
            parent_task_id,
            None,
            report_content,
            artifact_type="compat_parent_report",
        )

    @classmethod
    def save_child_report(
        cls, parent_task_id: int, child_task_id: int, report_content: str
    ) -> str:
        """
        保存子任务报告

        Args:
            parent_task_id: 父任务ID
            child_task_id: 子任务ID
            report_content: 报告内容

        Returns:
            报告相对路径
        """
        return artifact_store.save_report(
            parent_task_id,
            child_task_id,
            report_content,
            artifact_type="compat_child_report",
        )


# 全局报告生成器实例
report_builder = ReportBuilder()
