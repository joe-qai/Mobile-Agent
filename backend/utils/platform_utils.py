"""Platform utilities for cross-platform operations."""

import asyncio
import os
import subprocess
import sys
from asyncio.subprocess import Process as AsyncProcess


def is_windows() -> bool:
    """Check if running on Windows."""
    return sys.platform == "win32"


async def run_cmd_silently(cmd: list[str]) -> subprocess.CompletedProcess[bytes]:
    """Run a command silently (no output to stdout/stderr)."""
    return await asyncio.to_thread(
        subprocess.run,
        cmd,
        capture_output=True,
        timeout=30,
    )


async def spawn_process(
    cmd: list[str],
    capture_output: bool = False,
) -> subprocess.Popen[bytes] | AsyncProcess:
    """Spawn a subprocess asynchronously.

    Args:
        cmd: Command to run
        capture_output: Whether to capture stdout/stderr

    Returns:
        Subprocess object (Popen on Windows, AsyncProcess on Unix)
    """
    if is_windows():
        # On Windows, use subprocess.Popen directly due to asyncio.subprocess issues
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture_output else subprocess.DEVNULL,
        )
    else:
        # On Unix, use asyncio.create_subprocess_exec
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE if capture_output else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE if capture_output else asyncio.subprocess.DEVNULL,
        )
        return process