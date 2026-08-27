from **future** import annotations

import base64
import json
import threading
from dataclasses import dataclass
from typing import Any

class ChildSessionError(RuntimeError):
"""Raised when communication with the child-session agent fails."""

@dataclass(frozen=True)
class Screenshot:
png_bytes: bytes
width: int
height: int
session: int

class ChildSessionClient:
"""
Client for the persistent Alfred ChildInputAgent running
inside the isolated Windows child session.


Communication uses:

    \\\\.\\pipe\\Alfred.ChildInput.v1
"""

PIPE_NAME = r"\\.\pipe\Alfred.ChildInput.v1"

def __init__(self) -> None:
    self._pipe: Any = None
    self._reader: Any = None
    self._writer: Any = None

    self._lock = threading.RLock()

# ================================================================
# Connection
# ================================================================

def connect(self) -> None:
    with self._lock:
        if self._pipe is not None:
            return

        try:
            pipe = open(
                self.PIPE_NAME,
                mode="r+",
                encoding="utf-8",
                newline="\n",
                buffering=1,
            )
        except OSError as exc:
            raise ChildSessionError(
                "Could not connect to ChildInputAgent.\n"
                "Make sure ChildInputAgent is running "
                "inside Session 2.\n"
                f"Pipe: {self.PIPE_NAME}\n"
                f"Error: {exc}"
            ) from exc

        self._pipe = pipe
        self._reader = pipe
        self._writer = pipe

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
            payload
        )

        self._writer.flush()

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
            line
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
    Request the newest full-resolution child-session frame.

    The image is decoded into memory. No screenshot file is
    created on disk.
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

    if not isinstance(
        width,
        int,
    ):
        raise ChildSessionError(
            "Screenshot width is invalid."
        )

    if not isinstance(
        height,
        int,
    ):
        raise ChildSessionError(
            "Screenshot height is invalid."
        )

    if not isinstance(
        session,
        int,
    ):
        raise ChildSessionError(
            "Screenshot session is invalid."
        )

    if not isinstance(
        encoded,
        str,
    ):
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

