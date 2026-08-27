from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any


class DesktopBridgeError(RuntimeError):
    """Raised when the native DesktopBridge fails."""


class DesktopBridgeClient:
    """
    Persistent client for Alfred's native DesktopBridge.

    The native bridge is launched once as a compiled Windows
    executable and remains alive for the lifetime of this client.
    """

    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._stderr_thread: threading.Thread | None = None

        self._start_bridge()

    # ================================================================
    # Startup
    # ================================================================

    def _bridge_executable(self) -> Path:
        """
        Return the compiled DesktopBridge executable.

        Expected location:

            src/windows/native/DesktopBridge/
                bin/Release/net10.0/win-x64/publish/
                    DesktopBridge.exe
        """

        base_dir = (
            Path(__file__).resolve().parent
            / "native"
            / "DesktopBridge"
            / "bin"
            / "Release"
            / "net10.0"
            / "win-x64"
            / "publish"
        )

        executable = (
            base_dir
            / "DesktopBridge.exe"
        )

        if not executable.exists():
            raise FileNotFoundError(
                "DesktopBridge.exe was not found.\n"
                f"Expected: {executable}\n\n"
                "Build it with:\n"
                "dotnet publish -c Release "
                "-r win-x64 --self-contained false"
            )

        return executable

    def _start_bridge(self) -> None:
        executable = self._bridge_executable()

        self._process = subprocess.Popen(
            [str(executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            name="alfred-desktop-bridge-stderr",
            daemon=True,
        )

        self._stderr_thread.start()

        # Confirm the process is alive and accepting commands.
        result = self._request(
            "ping"
        )

        if result.get("pong") is not True:
            self.close()

            raise DesktopBridgeError(
                "DesktopBridge started but did not respond "
                "correctly to ping."
            )

    def _drain_stderr(self) -> None:
        """
        Continuously drain native stderr.

        This prevents the pipe from filling and blocking the
        native process.
        """

        process = self._process

        if process is None or process.stderr is None:
            return

        for line in process.stderr:
            line = line.rstrip()

            if line:
                print(
                    f"[DesktopBridge] {line}"
                )

    # ================================================================
    # IPC
    # ================================================================

    def _request(
        self,
        operation: str,
        **params: Any,
    ) -> dict[str, Any]:
        process = self._process

        if process is None:
            raise DesktopBridgeError(
                "DesktopBridge is not started."
            )

        if process.poll() is not None:
            raise DesktopBridgeError(
                "DesktopBridge process has exited "
                f"with code {process.returncode}."
            )

        if process.stdin is None:
            raise DesktopBridgeError(
                "DesktopBridge stdin is unavailable."
            )

        if process.stdout is None:
            raise DesktopBridgeError(
                "DesktopBridge stdout is unavailable."
            )

        request = {
            "op": operation,
            **params,
        }

        payload = (
            json.dumps(
                request,
                separators=(",", ":"),
            )
            + "\n"
        )

        with self._lock:
            try:
                process.stdin.write(
                    payload
                )

                process.stdin.flush()

                line = process.stdout.readline()

            except BrokenPipeError as exc:
                raise DesktopBridgeError(
                    "Connection to DesktopBridge was broken."
                ) from exc

        if not line:
            return_code = process.poll()

            raise DesktopBridgeError(
                "DesktopBridge closed stdout unexpectedly. "
                f"Exit code: {return_code}."
            )

        try:
            response = json.loads(
                line
            )

        except json.JSONDecodeError as exc:
            raise DesktopBridgeError(
                "DesktopBridge returned invalid JSON:\n"
                f"{line!r}"
            ) from exc

        if not isinstance(
            response,
            dict,
        ):
            raise DesktopBridgeError(
                "DesktopBridge returned a non-object response."
            )

        if response.get("ok") is False:
            code = response.get(
                "error",
                "unknown_error",
            )

            message = response.get(
                "message",
                "DesktopBridge request failed.",
            )

            raise DesktopBridgeError(
                f"{code}: {message}"
            )

        return response

    # ================================================================
    # Public operations
    # ================================================================

    def ping(self) -> dict[str, Any]:
        return self._request(
            "ping"
        )

    def count(self) -> int:
        response = self._request(
            "count"
        )

        return int(
            response["count"]
        )

    def current(self) -> int:
        response = self._request(
            "current"
        )

        return int(
            response["desktop"]
        )

    def window_desktop(
        self,
        hwnd: int,
    ) -> int:
        response = self._request(
            "window_desktop",
            hwnd=int(hwnd),
        )

        return int(
            response["desktop"]
        )

    def can_move(
        self,
        hwnd: int,
    ) -> bool:
        response = self._request(
            "can_move",
            hwnd=int(hwnd),
        )

        return bool(
            response["movable"]
        )

    def move_window(
        self,
        hwnd: int,
        desktop: int,
    ) -> dict[str, Any]:
        return self._request(
            "move_window",
            hwnd=int(hwnd),
            desktop=int(desktop),
        )

    # ================================================================
    # Lifecycle
    # ================================================================

    def close(self) -> None:
        process = self._process

        if process is None:
            return

        if process.poll() is None:
            try:
                self._request(
                    "shutdown"
                )
            except Exception:
                pass

        try:
            process.wait(
                timeout=1.0
            )
        except subprocess.TimeoutExpired:
            process.terminate()

            try:
                process.wait(
                    timeout=1.0
                )
            except subprocess.TimeoutExpired:
                process.kill()

                try:
                    process.wait(
                        timeout=1.0
                    )
                except subprocess.TimeoutExpired:
                    pass

        self._process = None

    def __enter__(
        self,
    ) -> DesktopBridgeClient:
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        self.close()