"""Scrcpy video streaming implementation (ya-webadb protocol aligned)."""

import asyncio
import os
import socket
import subprocess
import sys
import time
from asyncio.subprocess import Process as AsyncProcess
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path

from backend.scrcpy.scrcpy_protocol import (
    PTS_CONFIG,
    PTS_KEYFRAME,
    SCRCPY_CODEC_NAME_TO_ID,
    SCRCPY_KNOWN_CODECS,
    ScrcpyMediaStreamPacket,
    ScrcpyServerOptions,
    ScrcpyVideoStreamMetadata,
    ScrcpyVideoStreamOptions,
)
from backend.utils.logger import logger
from backend.utils.platform_utils import is_windows, run_cmd_silently, spawn_process


async def is_port_available(port: int, host: str = "127.0.0.1") -> bool:
    """Test if TCP port is available for binding.

    Args:
        port: TCP port number
        host: Host address to test

    Returns:
        True if port can be bound (available), False otherwise
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(False)
        sock.bind((host, port))
        logger.debug(f"Port {port} is available for binding")
        return True
    except OSError as e:
        logger.debug(f"Port {port} is occupied: {e}")
        return False
    finally:
        if sock:
            sock.close()


async def wait_for_port_release(
    port: int,
    timeout: float = 5.0,
    poll_interval: float = 0.2,
    host: str = "127.0.0.1",
) -> bool:
    """Wait for TCP port to become available with polling.

    Args:
        port: TCP port to wait for
        timeout: Maximum wait time in seconds (default: 5.0)
        poll_interval: Check interval in seconds (default: 0.2)
        host: Host address

    Returns:
        True if port became available, False if timeout
    """
    start_time = time.time()
    attempt = 0

    while time.time() - start_time < timeout:
        attempt += 1
        if await is_port_available(port, host):
            elapsed = time.time() - start_time
            logger.info(
                f"Port {port} became available after {elapsed:.2f}s ({attempt} checks)"
            )
            return True

        if attempt % 5 == 0:
            elapsed = time.time() - start_time
            logger.debug(f"Still waiting for port {port}... ({elapsed:.1f}s elapsed)")

        await asyncio.sleep(poll_interval)

    logger.warning(f"Port {port} did not release within {timeout}s timeout")
    return False


@dataclass(frozen=True)
class _ScrcpyLaunchProfile:
    """Scrcpy startup parameters for compatibility fallback."""

    name: str
    max_size: int
    bit_rate: int
    max_fps: int
    idr_interval_s: int
    include_codec_options: bool = True


class ScrcpyStreamer:
    """Manages scrcpy server lifecycle and video stream parsing."""

    DEFAULT_PORT = 27183

    def __init__(
        self,
        device_id: str | None = None,
        max_size: int = 720,
        bit_rate: int = 1_500_000,
        port: int = DEFAULT_PORT,
        idr_interval_s: int = 2,
        max_fps: int = 60,
        stream_options: ScrcpyVideoStreamOptions | None = None,
    ):
        """Initialize ScrcpyStreamer.

        Args:
            device_id: ADB device serial (None for default device)
            max_size: Maximum video dimension
            bit_rate: Video bitrate in bps
            port: TCP port for scrcpy socket
            idr_interval_s: Seconds between IDR frames (controls GOP length)
            stream_options: Scrcpy protocol options for metadata/frame parsing
        """
        self.device_id = device_id
        self.max_size = max_size
        self.bit_rate = bit_rate
        self.port = port
        self.idr_interval_s = idr_interval_s
        self.max_fps = max_fps
        self._include_codec_options = True
        self.stream_options = stream_options or ScrcpyVideoStreamOptions()

        self.scrcpy_process: subprocess.Popen[bytes] | AsyncProcess | None = None
        self.tcp_socket: socket.socket | None = None
        self.forward_cleanup_needed = False

        self._read_buffer = bytearray()
        self._metadata: ScrcpyVideoStreamMetadata | None = None
        self._dummy_byte_skipped = False
        self._should_stop = asyncio.Event()

        self.scrcpy_server_path = self._find_scrcpy_server()

    def _apply_launch_profile(self, profile: _ScrcpyLaunchProfile) -> None:
        """Apply a known-good scrcpy launch profile before starting the server."""
        logger.info(f"Applying scrcpy launch profile: {profile.name}")
        self.max_size = profile.max_size
        self.bit_rate = profile.bit_rate
        self.max_fps = profile.max_fps
        self.idr_interval_s = profile.idr_interval_s
        self._include_codec_options = profile.include_codec_options

    @staticmethod
    def _is_compatibility_retry_error(error: Exception) -> bool:
        message = str(error).lower()
        return (
            "socket closed by remote" in message
            or "connection reset by peer" in message
            or "connection aborted" in message
        )

    def _find_scrcpy_server(self) -> str:
        """Find scrcpy-server binary path with robust path resolution."""
        # Priority 1: PyInstaller bundled path
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled_server = Path(meipass) / "resources" / "scrcpy-server-v3.3.3"
            if bundled_server.exists():
                logger.info(f"Using bundled scrcpy-server: {bundled_server}")
                return str(bundled_server)

        # Priority 2: Project resources directory
        project_root = Path(__file__).resolve().parent.parent.parent
        project_server = project_root / "resources" / "scrcpy-server-v3.3.3"
        if project_server.exists():
            logger.info(f"Using project scrcpy-server: {project_server}")
            return str(project_server)

        # Priority 3: Backend resources directory (backend/resources/)
        backend_root = Path(__file__).resolve().parent.parent
        backend_server = backend_root / "resources" / "scrcpy-server-v3.3.3"
        if backend_server.exists():
            logger.info(f"Using backend scrcpy-server: {backend_server}")
            return str(backend_server)

        # Priority 4: Check scrcpy-server without version suffix
        project_server_no_version = project_root / "resources" / "scrcpy-server"
        if project_server_no_version.exists():
            logger.info(f"Using project scrcpy-server: {project_server_no_version}")
            return str(project_server_no_version)
        backend_server_no_version = backend_root / "resources" / "scrcpy-server"
        if backend_server_no_version.exists():
            logger.info(f"Using backend scrcpy-server: {backend_server_no_version}")
            return str(backend_server_no_version)

        # Priority 5: Same directory as this file
        local_server = Path(__file__).resolve().parent / "scrcpy-server-v3.3.3"
        if local_server.exists():
            logger.info(f"Using local scrcpy-server: {local_server}")
            return str(local_server)

        # Priority 6: Environment variable
        scrcpy_server = os.getenv("SCRCPY_SERVER_PATH")
        if scrcpy_server and os.path.exists(scrcpy_server):
            logger.info(f"Using env scrcpy-server: {scrcpy_server}")
            return scrcpy_server

        # Priority 7: Common system locations
        paths = [
            "/opt/homebrew/Cellar/scrcpy/3.3.3/share/scrcpy/scrcpy-server",
            "/usr/local/share/scrcpy/scrcpy-server",
            "/usr/share/scrcpy/scrcpy-server",
        ]

        for path in paths:
            if os.path.exists(path):
                logger.info(f"Using system scrcpy-server: {path}")
                return path

        raise FileNotFoundError(
            "scrcpy-server not found. Please put scrcpy-server-v3.3.3 in resources/ or set SCRCPY_SERVER_PATH."
        )

    async def start(self) -> ScrcpyVideoStreamMetadata:
        """Start scrcpy server and establish connection.

        Returns:
            ScrcpyVideoStreamMetadata with device info (device_name, width, height, codec)
        """
        try:
            await self._start_once()

        except Exception as e:
            if self._is_compatibility_retry_error(e):
                logger.warning(
                    "Scrcpy startup failed with a compatibility-sensitive socket "
                    f"error, retrying without codec options: {e}"
                )
                self.stop()
                self._apply_launch_profile(
                    _ScrcpyLaunchProfile(
                        name="no-codec-options",
                        max_size=self.max_size,
                        bit_rate=self.bit_rate,
                        max_fps=self.max_fps,
                        idr_interval_s=2,
                        include_codec_options=False,
                    )
                )
                try:
                    await self._start_once()
                except Exception as retry_error:
                    logger.error(f"Failed to start with compatibility profile: {retry_error}")
                    self.stop()
                    raise RuntimeError(
                        f"Failed to start scrcpy server: {retry_error}"
                    ) from retry_error

                return self._metadata

            logger.error(f"Failed to start: {e}")
            self.stop()
            raise RuntimeError(f"Failed to start scrcpy server: {e}") from e

        return self._metadata

    async def _start_once(self) -> None:
        self._read_buffer.clear()
        self._metadata = None
        self._dummy_byte_skipped = False
        self._should_stop.clear()
        logger.debug("Reset stream state")

        logger.info(f"Checking device {self.device_id} availability...")
        await self._check_device_available()
        logger.info(f"Device {self.device_id} is available")

        logger.info("Cleaning up existing scrcpy processes...")
        await self._cleanup_existing_server()

        logger.info("Pushing server to device...")
        await self._push_server()

        logger.info(f"Setting up port forwarding on port {self.port}...")
        await self._setup_port_forward()

        logger.info("Starting scrcpy server...")
        await self._start_server()

        logger.info("Connecting to TCP socket...")
        await self._connect_socket()
        logger.info("Successfully connected!")

        # Read video metadata immediately after connection.
        logger.debug("Reading video metadata immediately after connection...")
        await self.read_video_metadata()
        logger.debug("Video metadata read successfully")

    async def _check_device_available(self) -> None:
        """Check if device is available via ADB."""
        cmd = ["adb"]
        if self.device_id:
            cmd.extend(["-s", self.device_id])
        cmd.append("get-state")

        try:
            result = await asyncio.wait_for(run_cmd_silently(cmd), timeout=5.0)

            state = (
                result.stdout.strip().decode("utf-8", errors="replace")
                if result.stdout
                else ""
            )
            error_output = (
                result.stderr.strip().decode("utf-8", errors="replace")
                if result.stderr
                else ""
            )

            if "not found" in error_output.lower() or "offline" in error_output.lower():
                raise RuntimeError(
                    f"Device {self.device_id} is not available: {error_output}"
                )

            if state != "device":
                raise RuntimeError(
                    f"Device {self.device_id} is not available (state: {state or 'offline'})"
                )

            logger.debug(f"Device {self.device_id} is available (state: {state})")

        except asyncio.TimeoutError:
            raise RuntimeError(f"Device {self.device_id} connection timed out")
        except FileNotFoundError:
            raise RuntimeError("ADB executable not found")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to check device {self.device_id}: {e}")

    async def _cleanup_existing_server(self) -> None:
        """Kill existing scrcpy server processes and wait for port release."""
        cmd_base = ["adb"]
        if self.device_id:
            cmd_base.extend(["-s", self.device_id])

        logger.debug("Killing scrcpy processes via pkill...")
        cmd = cmd_base + ["shell", "pkill", "-9", "-f", "app_process.*scrcpy"]
        await run_cmd_silently(cmd)

        logger.debug("Killing scrcpy processes via PID...")
        cmd = cmd_base + [
            "shell",
            "ps -ef | grep 'app_process.*scrcpy' | grep -v grep | awk '{print $2}' | xargs kill -9",
        ]
        await run_cmd_silently(cmd)

        logger.debug(f"Removing ADB port forward on port {self.port}...")
        cmd_remove_forward = cmd_base + ["forward", "--remove", f"tcp:{self.port}"]
        await run_cmd_silently(cmd_remove_forward)

        logger.info(f"Waiting for port {self.port} to be released...")
        port_released = await wait_for_port_release(
            self.port,
            timeout=5.0,
            poll_interval=0.2,
        )

        if not port_released:
            logger.warning(
                f"Port {self.port} still occupied after cleanup. "
                "Will attempt to start anyway (may fail)."
            )
        else:
            logger.info(f"Port {self.port} successfully released and ready")

    async def _push_server(self) -> None:
        """Push scrcpy-server to device."""
        cmd = ["adb"]
        if self.device_id:
            cmd.extend(["-s", self.device_id])
        cmd.extend(["push", self.scrcpy_server_path, "/data/local/tmp/scrcpy-server"])

        await run_cmd_silently(cmd)

    async def _setup_port_forward(self) -> None:
        """Setup ADB port forwarding."""
        cmd = ["adb"]
        if self.device_id:
            cmd.extend(["-s", self.device_id])
        cmd.extend(["forward", f"tcp:{self.port}", "localabstract:scrcpy"])

        await run_cmd_silently(cmd)
        self.forward_cleanup_needed = True

    def _build_server_options(self) -> ScrcpyServerOptions:
        codec_options = (
            f"i-frame-interval={self.idr_interval_s}"
            if self._include_codec_options
            else None
        )
        return ScrcpyServerOptions(
            max_size=self.max_size,
            bit_rate=self.bit_rate,
            max_fps=self.max_fps,
            tunnel_forward=True,
            audio=False,
            control=False,
            cleanup=False,
            video_codec=self.stream_options.video_codec,
            send_frame_meta=self.stream_options.send_frame_meta,
            send_device_meta=self.stream_options.send_device_meta,
            send_codec_meta=self.stream_options.send_codec_meta,
            send_dummy_byte=self.stream_options.send_dummy_byte,
            video_codec_options=codec_options,
        )

    async def _start_server(self) -> None:
        """Start scrcpy server on device with intelligent retry."""
        max_retries = 3
        retry_delay = 1.0

        options = self._build_server_options()

        for attempt in range(max_retries):
            cmd = ["adb"]
            if self.device_id:
                cmd.extend(["-s", self.device_id])

            server_args = [
                "shell",
                "CLASSPATH=/data/local/tmp/scrcpy-server",
                "app_process",
                "/",
                "com.genymobile.scrcpy.Server",
                "3.3.3",
                f"max_size={options.max_size}",
                f"video_bit_rate={options.bit_rate}",
                f"max_fps={options.max_fps}",
                f"tunnel_forward={str(options.tunnel_forward).lower()}",
                f"audio={str(options.audio).lower()}",
                f"control={str(options.control).lower()}",
                f"cleanup={str(options.cleanup).lower()}",
                f"video_codec={options.video_codec}",
                f"send_frame_meta={str(options.send_frame_meta).lower()}",
                f"send_device_meta={str(options.send_device_meta).lower()}",
                f"send_codec_meta={str(options.send_codec_meta).lower()}",
                f"send_dummy_byte={str(options.send_dummy_byte).lower()}",
            ]
            if options.video_codec_options is not None:
                server_args.append(f"video_codec_options={options.video_codec_options}")
            cmd.extend(server_args)

            self.scrcpy_process = await spawn_process(cmd, capture_output=True)

            # 减少启动等待时间，使用更短的延迟让服务器有时间初始化
            await asyncio.sleep(0.5)

            proc = self.scrcpy_process
            error_msg = None
            if proc:
                poll_result = proc.poll() if is_windows() else proc.returncode
                logger.info(
                    f"Server process poll after 2s: {poll_result} (None=running)"
                )

                if poll_result is not None:
                    try:
                        if is_windows():
                            stdout_bytes = proc.stdout.read() if proc.stdout else b""
                            stderr_bytes = proc.stderr.read() if proc.stderr else b""
                        else:
                            stdout_bytes, stderr_bytes = await proc.communicate()
                        error_msg = stderr_bytes.decode(
                            errors="replace"
                        ) or stdout_bytes.decode(errors="replace")
                    except Exception as e:
                        error_msg = f"(could not read process output: {e})"

            if error_msg is not None and error_msg.strip():
                error_msg = error_msg.strip()
                if "Address already in use" in error_msg:
                    logger.error(
                        f"Port {self.port} conflict detected (attempt {attempt + 1}/{max_retries}). "
                        f"Error: {error_msg[:200]}"
                    )
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"Retrying with aggressive cleanup in {retry_delay}s..."
                        )
                        await self._cleanup_existing_server()
                        await asyncio.sleep(retry_delay)
                        continue
                    raise RuntimeError(
                        f"Port {self.port} persistently occupied after {max_retries} attempts. "
                        "Please check if another scrcpy instance is running."
                    )
                else:
                    logger.error(f"Scrcpy server startup failed: {error_msg[:200]}")
                    raise RuntimeError(f"Scrcpy server failed to start: {error_msg}")

            if poll_result is not None:
                raise RuntimeError(
                    f"Scrcpy server process exited unexpectedly (code={poll_result})"
                )

            logger.info("Scrcpy server started successfully")
            return

        raise RuntimeError("Failed to start scrcpy server after maximum retries")

    async def _connect_socket(self) -> None:
        """Connect to scrcpy TCP socket with exponential backoff."""
        max_attempts = 10
        retry_delay = 0.3

        for attempt in range(max_attempts):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)

            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2 * 1024 * 1024)
            except OSError as e:
                logger.debug(f"Failed to set socket buffer size: {e}")

            try:
                sock.connect(("localhost", self.port))
                sock.settimeout(None)
                self.tcp_socket = sock
                logger.debug(f"Connected to scrcpy server on attempt {attempt + 1}")
                return
            except (ConnectionRefusedError, OSError) as e:
                try:
                    sock.close()
                except Exception:
                    pass

                if attempt < max_attempts - 1:
                    logger.debug(
                        f"Connection attempt {attempt + 1}/{max_attempts} failed: {e}. "
                        f"Retrying in {retry_delay}s..."
                    )
                    await asyncio.sleep(retry_delay)
                    if attempt >= 3:
                        retry_delay = 0.5
                else:
                    logger.error(
                        f"Failed to connect after {max_attempts} attempts. "
                        f"Last error: {e}"
                    )

        raise ConnectionError("Failed to connect to scrcpy server")

    async def _read_exactly(self, size: int) -> bytes:
        sock = self.tcp_socket
        if sock is None:
            raise ConnectionError("Socket not connected")

        while len(self._read_buffer) < size:
            try:
                chunk = await asyncio.to_thread(
                    sock.recv, max(4096, size - len(self._read_buffer))
                )
            except OSError as e:
                raise ConnectionError(f"Socket error: {e}") from e
            if not chunk:
                raise ConnectionError("Socket closed by remote")
            self._read_buffer.extend(chunk)

        data = bytes(self._read_buffer[:size])
        del self._read_buffer[:size]
        return data

    async def _read_u16(self) -> int:
        return int.from_bytes(await self._read_exactly(2), "big")

    async def _read_u32(self) -> int:
        return int.from_bytes(await self._read_exactly(4), "big")

    async def _read_u64(self) -> int:
        return int.from_bytes(await self._read_exactly(8), "big")

    async def read_video_metadata(self) -> ScrcpyVideoStreamMetadata:
        """Read and cache video stream metadata from scrcpy."""
        if self._metadata is not None:
            return self._metadata

        if self.stream_options.send_dummy_byte and not self._dummy_byte_skipped:
            await self._read_exactly(1)
            self._dummy_byte_skipped = True

        device_name = None
        width = None
        height = None
        codec = SCRCPY_CODEC_NAME_TO_ID.get(
            self.stream_options.video_codec, SCRCPY_CODEC_NAME_TO_ID["h264"]
        )

        if self.stream_options.send_device_meta:
            raw_name = await self._read_exactly(64)
            device_name = raw_name.split(b"\x00", 1)[0].decode(
                "utf-8", errors="replace"
            )

        if self.stream_options.send_codec_meta:
            codec_value = await self._read_u32()
            if codec_value in SCRCPY_KNOWN_CODECS:
                codec = codec_value
                width = await self._read_u32()
                height = await self._read_u32()
            else:
                width = (codec_value >> 16) & 0xFFFF
                height = codec_value & 0xFFFF
        else:
            if self.stream_options.send_device_meta:
                width = await self._read_u16()
                height = await self._read_u16()

        self._metadata = ScrcpyVideoStreamMetadata(
            device_name=device_name,
            width=width,
            height=height,
            codec=codec,
        )
        return self._metadata

    async def read_media_packet(self) -> ScrcpyMediaStreamPacket:
        """Read one Scrcpy media packet (configuration/data)."""
        if not self.stream_options.send_frame_meta:
            raise RuntimeError(
                "send_frame_meta is disabled; packet parsing unavailable"
            )

        if self._metadata is None:
            await self.read_video_metadata()

        pts = await self._read_u64()
        data_length = await self._read_u32()
        payload = await self._read_exactly(data_length)

        # logger.debug(f"Read packet: PTS={pts}, length={data_length}")

        if pts == PTS_CONFIG:
            logger.debug(f"Configuration packet received (size: {len(payload)})")
            return ScrcpyMediaStreamPacket(type="configuration", data=payload)

        if pts & PTS_KEYFRAME:
            logger.debug(
                f"Keyframe packet received (size: {len(payload)}, PTS: {pts & ~PTS_KEYFRAME})"
            )
            return ScrcpyMediaStreamPacket(
                type="data",
                data=payload,
                keyframe=True,
                pts=pts & ~PTS_KEYFRAME,
            )

        # logger.debug(f"Data packet received (size: {len(payload)}, PTS: {pts})")
        return ScrcpyMediaStreamPacket(
            type="data",
            data=payload,
            keyframe=False,
            pts=pts,
        )

    async def iter_packets(self) -> AsyncGenerator[ScrcpyMediaStreamPacket, None]:
        """Yield packets continuously from the scrcpy stream."""
        try:
            while True:
                if self._should_stop.is_set():
                    logger.info("Streamer stopped, exiting packet iterator")
                    return
                yield await self.read_media_packet()
        except (ConnectionError, asyncio.CancelledError) as exc:
            if self._should_stop.is_set():
                logger.info("Streamer stopped, exiting packet iterator")
                return
            logger.info("Connection closed or cancelled, exiting packet iterator")
            raise exc
        except OSError as e:
            if self._should_stop.is_set():
                logger.info("Socket closed during shutdown, exiting packet iterator")
                return
            logger.error(f"Socket OS error in packet iterator: {e}")
            raise ConnectionError(f"Socket error: {e}") from e
        except Exception as exc:
            logger.error(f"Unexpected error in packet iterator: {exc}")
            raise

    def stop(self) -> None:
        """Stop scrcpy server and cleanup resources."""
        self._should_stop.set()

        sock = self.tcp_socket
        self.tcp_socket = None
        if sock:
            try:
                sock.close()
            except OSError as exc:
                logger.debug("Failed to close scrcpy socket: %s", exc)

        if self.scrcpy_process:
            try:
                self.scrcpy_process.terminate()
                if isinstance(self.scrcpy_process, subprocess.Popen):
                    self.scrcpy_process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired) as exc:
                logger.debug("Graceful scrcpy shutdown failed: %s", exc)
                try:
                    self.scrcpy_process.kill()
                except OSError as kill_exc:
                    logger.debug("Failed to kill scrcpy process: %s", kill_exc)
            self.scrcpy_process = None

        if self.forward_cleanup_needed:
            try:
                cmd = ["adb"]
                if self.device_id:
                    cmd.extend(["-s", self.device_id])
                cmd.extend(["forward", "--remove", f"tcp:{self.port}"])
                subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                logger.debug(
                    "Failed to remove adb forward for port %s: %s",
                    self.port,
                    exc,
                )
            self.forward_cleanup_needed = False

    def __del__(self):
        try:
            self.stop()
        except Exception as exc:
            try:
                logger.debug("Ignored scrcpy cleanup error during finalization: %s", exc)
            except Exception:
                pass
