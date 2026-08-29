from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR = _ROOT / "logs"
_LOG_FILE = _LOG_DIR / "alfred.log"
_MAX_BYTES = 5 * 1024 * 1024
_KEEP = 3


class _Tee:
    """Write to the real stream and mirror into the log file."""

    def __init__(self, stream, handle) -> None:
        self._stream = stream
        self._handle = handle

    def write(self, text: str) -> int:
        n = self._stream.write(text)
        try:
            self._handle.write(text)
            self._handle.flush()
        except Exception:  # noqa: BLE001
            pass
        return n

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:  # noqa: BLE001
            pass

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _rotate() -> None:
    try:
        if _LOG_FILE.exists() and _LOG_FILE.stat().st_size > _MAX_BYTES:
            for i in range(_KEEP - 1, 0, -1):
                src = _LOG_FILE.with_suffix(f".log.{i}")
                dst = _LOG_FILE.with_suffix(f".log.{i + 1}")
                if src.exists():
                    src.replace(dst)
            _LOG_FILE.replace(_LOG_FILE.with_suffix(".log.1"))
    except Exception:  # noqa: BLE001
        pass


_configured = False


def configure_logging() -> Path:
    """
    Send everything Alfred prints or logs to logs/alfred.log (rotated),
    while still showing it on the console. Call once, early, from an
    entry point. Safe to call more than once.
    """

    global _configured
    if _configured:
        return _LOG_FILE

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _rotate()

    handle = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)
    handle.write(
        f"\n===== Alfred started {datetime.now(timezone.utc).isoformat()} "
        f"(pid {os.getpid()}) =====\n"
    )

    sys.stdout = _Tee(sys.stdout, handle)
    sys.stderr = _Tee(sys.stderr, handle)

    level = getattr(
        logging, os.getenv("ALFRED_LOG_LEVEL", "INFO").upper(), logging.INFO
    )
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    _configured = True
    return _LOG_FILE


def log_path() -> Path:
    return _LOG_FILE
