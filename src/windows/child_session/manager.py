from __future__ import annotations

import ctypes
import subprocess
import threading
from pathlib import Path

from src.windows.quiet import NO_WINDOW


class ChildSessionError(RuntimeError):
    """Raised when Alfred's child-session infrastructure fails."""


class ChildSessionManager:
    """
    Owns Alfred's persistent child-session host.

    The native host will eventually manage:
      - child-session creation
      - RDP ActiveX connection
      - screen transport
      - input transport

    This Python class intentionally keeps the interface small.
    """

    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.RLock()

    @property
    def is_running(self) -> bool:
        process = self._process

        return (
            process is not None
            and process.poll() is None
        )

    def probe_enabled(self) -> bool:
        """
        Check whether Windows child sessions are enabled.

        Uses the native WTS API directly for a cheap read-only check.
        """

        enabled = ctypes.c_bool(False)

        wtsapi32 = ctypes.WinDLL(
            "wtsapi32.dll"
        )

        function = (
            wtsapi32
            .WTSIsChildSessionsEnabled
        )

        function.argtypes = [
            ctypes.POINTER(
                ctypes.c_bool
            )
        ]

        function.restype = ctypes.c_bool

        success = function(
            ctypes.byref(enabled)
        )

        if not success:
            error = ctypes.get_last_error()

            raise ChildSessionError(
                "WTSIsChildSessionsEnabled failed "
                f"with Win32 error {error}."
            )

        return bool(enabled.value)

    def start(self) -> None:
        """
        Start the persistent native child-session host.

        The actual RDP/ActiveX host will live in a compiled native
        executable. It is intentionally started only once.
        """

        with self._lock:
            if self.is_running:
                return

            executable = (
                Path(__file__).resolve().parent.parent
                / "native"
                / "ChildSessionHost"
                / "bin"
                / "Release"
                / "net48"
                / "ChildSessionHost.exe"
            )

            if not executable.exists():
                raise FileNotFoundError(
                    "ChildSessionHost.exe was not found.\n"
                    f"Expected: {executable}"
                )

            self._process = subprocess.Popen(
                [str(executable)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=NO_WINDOW,
            )

    def stop(self) -> None:
        with self._lock:
            process = self._process

            if process is None:
                return

            if process.poll() is None:
                process.terminate()

                try:
                    process.wait(
                        timeout=2
                    )
                except subprocess.TimeoutExpired:
                    process.kill()

                    try:
                        process.wait(
                            timeout=1
                        )
                    except subprocess.TimeoutExpired:
                        pass

            self._process = None

    def __enter__(
        self,
    ) -> ChildSessionManager:
        self.start()
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> None:
        self.stop()
