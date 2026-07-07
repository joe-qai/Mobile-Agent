import os
import re
import subprocess
import sys
import tempfile
import uuid
from typing import Dict, Optional


class PythonExecutor:
    @staticmethod
    def _pytest_output_failed(output: str) -> bool:
        """Detect pytest failures when pytest.main() was called without sys.exit()."""
        if "test session starts" not in output:
            return False
        return bool(
            re.search(r"(^|\n)(FAILED|ERROR)\s+", output)
            or re.search(r"=+\s*(FAILURES|ERRORS)\s*=+", output)
            or re.search(r"=+.*\b\d+\s+(failed|error|errors)\b", output, re.IGNORECASE)
        )

    @staticmethod
    def _extract_pytest_failures(output: str) -> str:
        """Extract pytest failure/error details from stdout for display."""
        lines = output.splitlines()
        in_failure = False
        failure_lines = []
        for line in lines:
            if re.search(r"^={2,}\s*(FAILURES|ERRORS)\s*={2,}", line):
                in_failure = True
            if in_failure:
                failure_lines.append(line)
                if re.search(r"^={2,}\s*\d+ passed", line):
                    break
                if re.search(r"^={2,}\s*short test summary", line):
                    break
                if re.search(r"^={2,}\s*.*seconds?\s*=+", line):
                    break
        if failure_lines:
            return "\n".join(failure_lines)
        return ""

    def execute(
        self, script_content: str, device_id: Optional[str] = None, timeout: int = 300
    ) -> Dict:
        tmpfile = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                prefix=f"script_{uuid.uuid4().hex}_",
                delete=False,
                encoding="utf-8",
            ) as f:
                f.write(script_content)
                tmpfile = f.name

            env = os.environ.copy()
            if device_id:
                env["DEVICE_ID"] = device_id

            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            project_root = project_root.replace("\\", "/")
            current_pythonpath = env.get("PYTHONPATH", "")
            if current_pythonpath:
                env["PYTHONPATH"] = f"{project_root};{current_pythonpath}"
            else:
                env["PYTHONPATH"] = project_root

            result = subprocess.run(
                [sys.executable, tmpfile],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                encoding="utf-8",
                errors="replace",  # 替换无法解码的字符，避免 UnicodeDecodeError
            )

            effective_returncode = result.returncode
            error = result.stderr if result.stderr else None
            if result.returncode == 0 and self._pytest_output_failed(result.stdout):
                effective_returncode = 1
                if not error:
                    pytest_failures = self._extract_pytest_failures(result.stdout)
                    error = pytest_failures or "Test execution failed (see output for details)"

            return {
                "success": effective_returncode == 0,
                "output": result.stdout,
                "error": error,
                "returncode": effective_returncode,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "output": "",
                "error": f"Script timed out after {timeout}s",
                "returncode": -1,
            }
        except Exception as e:
            return {
                "success": False,
                "output": "",
                "error": str(e),
                "returncode": -1,
            }
        finally:
            if tmpfile and os.path.exists(tmpfile):
                try:
                    os.unlink(tmpfile)
                except OSError:
                    pass


python_executor = PythonExecutor()
