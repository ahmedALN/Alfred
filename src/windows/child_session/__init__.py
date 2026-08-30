from __future__ import annotations

import base64
import json
import threading
from dataclasses import dataclass
from typing import Any


class ChildSessionError(RuntimeError):
    """Raised when communication with the child-session agent fails."""


@dataclass(frozen=True)
class Screenshot:
    """A screenshot captured from the isolated child session."""

    png_bytes: bytes
    width: int
    height: int
    session: int

    @property
    def mime_type(self) -> str:
        return "image/png"


def child_session_id() -> int | None:
    """The id of Alfred's isolated child session, if one is running.

    Read-only; returns None when there is no child session (which is the
    normal state until the child-session host connects one).
    """
    import ctypes

    try:
        wtsapi32 = ctypes.WinDLL("wtsapi32.dll")
        fn = wtsapi32.WTSGetChildSessionId
        fn.argtypes = [ctypes.POINTER(ctypes.c_ulong)]
        fn.restype = ctypes.c_bool
        sid = ctypes.c_ulong(0)
        if fn(ctypes.byref(sid)) and sid.value not in (0, 0xFFFFFFFF):
            return int(sid.value)
    except Exception:  # noqa: BLE001
        pass
    return None


def current_session_id() -> int | None:
    """The session this Python process is running in."""
    import ctypes

    try:
        pid = ctypes.windll.kernel32.GetCurrentProcessId()
        sid = ctypes.c_ulong(0)
        if ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(sid)):
            return int(sid.value)
    except Exception:  # noqa: BLE001
        pass
    return None


class ChildSessionClient:
    """
    Client for the persistent Alfred ChildInputAgent.

    An agent instance runs in each Windows session that has one, and
    each listens on a session-scoped pipe:

        \\\\.\\pipe\\Alfred.ChildInput.v1.s<sessionId>

    ``target`` decides which desktop Alfred acts on:
      "child"   - Alfred's isolated session (does not disturb the user);
                  falls back to the current session if none is running
      "current" - this session, i.e. the user's own desktop
      an int    - that specific session id
    """

    PIPE_BASE = r"\\.\pipe\Alfred.ChildInput.v1"

    def __init__(self, target: str | int = "current") -> None:
        self._pipe: Any = None
        self._reader: Any = None
        self._writer: Any = None
        self._target = target

        self._lock = threading.RLock()

    @property
    def PIPE_NAME(self) -> str:  # noqa: N802 - kept for callers/tests
        return self._resolve_pipe()

    def _resolve_pipe(self) -> str:
        target = self._target
        if isinstance(target, int):
            session = target
        elif target == "child":
            session = child_session_id() or current_session_id()
        else:
            session = current_session_id()
        if session is None:
            # Last resort: the legacy un-suffixed name.
            return self.PIPE_BASE
        return f"{self.PIPE_BASE}.s{session}"

    # ================================================================
    # Connection
    # ================================================================

    def connect(self) -> None:
        """
        Connect to the already-running ChildInputAgent.

        Note:
        Windows named pipes are not ordinary files. We therefore
        use the Windows API directly instead of Python open().
        """

        with self._lock:
            if self._pipe is not None:
                return

            import ctypes

            kernel32 = ctypes.WinDLL(
                "kernel32",
                use_last_error=True,
            )

            CreateFileW = kernel32.CreateFileW
            CreateFileW.argtypes = [
                ctypes.c_wchar_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
            ]
            CreateFileW.restype = ctypes.c_void_p

            GENERIC_READ = 0x80000000
            GENERIC_WRITE = 0x40000000
            OPEN_EXISTING = 3

            ERROR_FILE_NOT_FOUND = 2
            ERROR_PIPE_BUSY = 231

            WaitNamedPipeW = kernel32.WaitNamedPipeW
            WaitNamedPipeW.argtypes = [
                ctypes.c_wchar_p,
                ctypes.c_uint32,
            ]
            WaitNamedPipeW.restype = ctypes.c_bool

            invalid_handle = ctypes.c_void_p(-1).value

            import time as _time

            handle = None
            error = 0
            deadline = _time.monotonic() + 5.0

            while _time.monotonic() < deadline:
                handle = CreateFileW(
                    self.PIPE_NAME,
                    GENERIC_READ | GENERIC_WRITE,
                    0,
                    None,
                    OPEN_EXISTING,
                    0,
                    None,
                )

                if handle and handle != invalid_handle:
                    break

                error = ctypes.get_last_error()

                if error == ERROR_PIPE_BUSY:
                    WaitNamedPipeW(self.PIPE_NAME, 2000)
                    continue

                if error == ERROR_FILE_NOT_FOUND:
                    _time.sleep(0.3)
                    continue

                break

            if not handle or handle == invalid_handle:
                raise ChildSessionError(
                    "Could not connect to ChildInputAgent.\n"
                    "Make sure ChildInputAgent is running "
                    "inside Session 2.\n"
                    f"Pipe: {self.PIPE_NAME}\n"
                    f"Win32 error: {error}"
                )

            self._pipe = _WindowsPipe(
                handle
            )

            self._reader = self._pipe
            self._writer = self._pipe

            try:
                response = self._request_locked(
                    {
                        "op": "ping"
                    }
                )

                if response.get("ok") is not True:
                    raise ChildSessionError(
                        "ChildInputAgent ping failed."
                    )

            except Exception:
                self._close_locked()
                raise

    # ================================================================
    # Low-level IPC
    # ================================================================

    def _request_locked(
        self,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        if self._pipe is None:
            raise ChildSessionError(
                "ChildInputAgent is not connected."
            )

        payload = (
            json.dumps(
                request,
                separators=(",", ":"),
            )
            + "\n"
        )

        try:
            self._writer.write(
                payload.encode("utf-8")
            )

            line = self._reader.readline()

        except Exception as exc:
            self._close_locked()

            raise ChildSessionError(
                f"ChildInputAgent pipe I/O failed: {exc}"
            ) from exc

        if not line:
            self._close_locked()

            raise ChildSessionError(
                "ChildInputAgent disconnected."
            )

        try:
            response = json.loads(
                line.decode("utf-8")
            )
        except json.JSONDecodeError as exc:
            raise ChildSessionError(
                "ChildInputAgent returned invalid JSON.\n"
                f"Response: {line!r}"
            ) from exc

        if not isinstance(
            response,
            dict,
        ):
            raise ChildSessionError(
                "ChildInputAgent returned a non-object response."
            )

        if response.get("ok") is False:
            code = response.get(
                "error",
                "unknown_error",
            )

            message = response.get(
                "message",
                "ChildInputAgent request failed.",
            )

            raise ChildSessionError(
                f"{code}: {message}"
            )

        return response

    def _request(
        self,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            if self._pipe is None:
                self.connect()

            return self._request_locked(
                request
            )

    # ================================================================
    # Session
    # ================================================================

    def ping(self) -> dict[str, Any]:
        return self._request(
            {
                "op": "ping"
            }
        )

    def session(self) -> int:
        response = self._request(
            {
                "op": "session"
            }
        )

        data = response.get(
            "data"
        )

        if not isinstance(
            data,
            dict,
        ):
            raise ChildSessionError(
                "Invalid child-session response."
            )

        value = data.get(
            "session"
        )

        if not isinstance(
            value,
            int,
        ):
            raise ChildSessionError(
                "Invalid child-session ID."
            )

        return value

    # ================================================================
    # Capture
    # ================================================================

    def capture_start(self) -> dict[str, Any]:
        return self._request(
            {
                "op": "capture_start"
            }
        )

    def capture_stop(self) -> dict[str, Any]:
        return self._request(
            {
                "op": "capture_stop"
            }
        )

    def screenshot(self) -> Screenshot:
        """
        Request the latest full-resolution child-session screenshot.

        The PNG is decoded into memory only.
        No screenshot file is created.
        """

        response = self._request(
            {
                "op": "screenshot"
            }
        )

        data = response.get(
            "data"
        )

        if not isinstance(
            data,
            dict,
        ):
            raise ChildSessionError(
                "Screenshot response has invalid data."
            )

        mime_type = data.get(
            "mime_type"
        )

        if mime_type != "image/png":
            raise ChildSessionError(
                "Unsupported screenshot MIME type: "
                f"{mime_type!r}"
            )

        width = data.get(
            "width"
        )

        height = data.get(
            "height"
        )

        session = data.get(
            "session"
        )

        encoded = data.get(
            "image_base64"
        )

        if not isinstance(width, int):
            raise ChildSessionError(
                "Screenshot width is invalid."
            )

        if not isinstance(height, int):
            raise ChildSessionError(
                "Screenshot height is invalid."
            )

        if not isinstance(session, int):
            raise ChildSessionError(
                "Screenshot session is invalid."
            )

        if not isinstance(encoded, str):
            raise ChildSessionError(
                "Screenshot image data is missing."
            )

        try:
            png_bytes = base64.b64decode(
                encoded,
                validate=True,
            )
        except Exception as exc:
            raise ChildSessionError(
                "Screenshot Base64 decoding failed."
            ) from exc

        if not png_bytes:
            raise ChildSessionError(
                "ChildInputAgent returned an empty screenshot."
            )

        return Screenshot(
            png_bytes=png_bytes,
            width=width,
            height=height,
            session=session,
        )

    # ================================================================
    # Input
    # ================================================================

    def activate(
        self,
        hwnd: int,
    ) -> dict[str, Any]:
        return self._request(
            {
                "op": "activate",
                "hwnd": int(hwnd),
            }
        )

    def mouse_move(
        self,
        x: int,
        y: int,
    ) -> dict[str, Any]:
        return self._request(
            {
                "op": "mouse_move",
                "x": int(x),
                "y": int(y),
            }
        )

    def click(
        self,
        button: str = "left",
    ) -> dict[str, Any]:
        return self._request(
            {
                "op": "click",
                "button": button,
            }
        )

    def type_text(
        self,
        text: str,
    ) -> dict[str, Any]:
        return self._request(
            {
                "op": "type",
                "text": text,
            }
        )

    def capture_window(self, hwnd: int) -> dict[str, Any]:
        """
        Point the capture at one window (by HWND). Subsequent
        screenshot() calls grab that window - even when it's on an
        inactive virtual desktop, so no desktop switch / flicker.
        """
        return self._request({"op": "capture_window", "hwnd": int(hwnd)})

    def key(
        self,
        keys: list[str] | str,
    ) -> dict[str, Any]:
        return self._request(
            {
                "op": "key",
                "keys": keys,
            }
        )

    def scroll(
        self,
        x: int,
        y: int,
        dy: int = -3,
    ) -> dict[str, Any]:
        return self._request(
            {
                "op": "scroll",
                "x": int(x),
                "y": int(y),
                "dy": int(dy),
            }
        )

    def drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
    ) -> dict[str, Any]:
        return self._request(
            {
                "op": "drag",
                "x1": int(x1),
                "y1": int(y1),
                "x2": int(x2),
                "y2": int(y2),
            }
        )

    def shutdown(self) -> dict[str, Any]:
        return self._request({"op": "shutdown"})

    # ================================================================
    # Lifecycle
    # ================================================================

    def _close_locked(self) -> None:
        pipe = self._pipe

        self._pipe = None
        self._reader = None
        self._writer = None

        if pipe is not None:
            try:
                pipe.close()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def __enter__(
        self,
    ) -> "ChildSessionClient":
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        self.close()


class _WindowsPipe:
    """
    Minimal synchronous wrapper around a Windows named-pipe HANDLE.
    """

    def __init__(
        self,
        handle: Any,
    ) -> None:
        import ctypes

        self._ctypes = ctypes
        self._handle = handle

        kernel32 = ctypes.WinDLL(
            "kernel32",
            use_last_error=True,
        )

        self._kernel32 = kernel32

        self._read_file = kernel32.ReadFile
        self._read_file.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        self._read_file.restype = ctypes.c_bool

        self._write_file = kernel32.WriteFile
        self._write_file.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        self._write_file.restype = ctypes.c_bool

        self._close_handle = kernel32.CloseHandle
        self._close_handle.argtypes = [
            ctypes.c_void_p
        ]
        self._close_handle.restype = ctypes.c_bool

        self._buffer = bytearray()

    def write(
        self,
        data: bytes,
    ) -> None:
        if not data:
            return

        ctypes = self._ctypes

        buffer = ctypes.create_string_buffer(
            data
        )

        written = ctypes.c_uint32(
            0
        )

        ok = self._write_file(
            self._handle,
            buffer,
            len(data),
            ctypes.byref(written),
            None,
        )

        if not ok:
            error = ctypes.get_last_error()

            raise OSError(
                error,
                f"WriteFile failed with Win32 error {error}."
            )

        if written.value != len(data):
            raise OSError(
                f"WriteFile wrote only "
                f"{written.value} of {len(data)} bytes."
            )

    def readline(self) -> bytes:
        """
        Read until the newline that terminates a JSON response.
        """

        while True:
            newline_index = self._buffer.find(
                b"\n"
            )

            if newline_index >= 0:
                line = bytes(
                    self._buffer[
                        :newline_index
                    ]
                )

                del self._buffer[
                    :newline_index + 1
                ]

                return line

            chunk = self._read_chunk()

            if not chunk:
                return b""

            self._buffer.extend(
                chunk
            )

    def _read_chunk(
        self,
    ) -> bytes:
        ctypes = self._ctypes

        buffer_size = 64 * 1024

        buffer = ctypes.create_string_buffer(
            buffer_size
        )

        bytes_read = ctypes.c_uint32(
            0
        )

        ok = self._read_file(
            self._handle,
            buffer,
            buffer_size,
            ctypes.byref(bytes_read),
            None,
        )

        if not ok:
            error = ctypes.get_last_error()

            raise OSError(
                error,
                f"ReadFile failed with Win32 error {error}."
            )

        if bytes_read.value == 0:
            return b""

        return buffer.raw[
            :bytes_read.value
        ]

    def close(self) -> None:
        if self._handle is None:
            return

        try:
            self._close_handle(
                self._handle
            )
        finally:
            self._handle = None
