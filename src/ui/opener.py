"""Opening the interface, and only ever having one of it.

Asked twice, this shows the window that already exists rather than
drawing a second one. The window process hides itself when closed and
keeps the page loaded, so "show" is immediate where "spawn" would mean
a cold start, a fresh socket and a blank screen for a second.

The signal to show travels over the websocket the page is already
holding: Alfred publishes it on the bus, the page hears it and asks
pywebview to raise the window it is drawn in. No second channel, no
port, nothing else listening.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from src.ui.live import BUS
from src.ui.server import INTERFACE

_ROOT = Path(__file__).resolve().parent.parent.parent

_process: subprocess.Popen | None = None
_lock = threading.Lock()


def _python() -> str:
    """The windowless interpreter, so no console flashes up."""
    here = Path(sys.executable)
    quiet = here.with_name("pythonw.exe")
    return str(quiet if quiet.exists() else here)


def is_open() -> bool:
    with _lock:
        return _process is not None and _process.poll() is None


def open_interface(force_new: bool = False) -> dict:
    """Show Alfred's interface. Returns what happened, in words."""
    global _process

    url = INTERFACE.start() if not INTERFACE.running else INTERFACE.url

    with _lock:
        alive = _process is not None and _process.poll() is None

        if alive and not force_new:
            # Already drawn, possibly hidden behind everything. The
            # page is listening for this.
            BUS.publish("show_window")
            return {"status": "success", "what": "shown", "url": url}

        if alive and force_new:
            try:
                _process.terminate()
            except Exception:  # noqa: BLE001
                pass
            _process = None

        if not alive:
            # A window left over from a previous Alfred is worse than
            # no window: the token is generated fresh each start, so
            # that one can never authenticate again and would sit there
            # saying "offline" for ever. It cannot be adopted, so it
            # goes, and a new one is drawn against the current key.
            _clear_orphans()

        flags = 0
        if sys.platform == "win32":
            # Its own process group, so a Ctrl-C aimed at Alfred does
            # not also kill the window, and no console is inherited.
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            _process = subprocess.Popen(
                [_python(), "-m", "src.ui", url],
                cwd=str(_ROOT),
                creationflags=flags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ, "PYTHONPATH": str(_ROOT)},
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"could not open it: {exc}"}

    return {"status": "success", "what": "opened", "url": url}


def _clear_orphans() -> int:
    """Windows left by an Alfred that is no longer running.

    They survive a crash holding a key that died with it, so they can
    never authenticate again and would sit there saying "offline".

    The matching is exact on purpose. The first version of this joined
    the command line into a string and looked for "src.ui" and "http"
    anywhere in it, which matched the shell it was launched from -
    every process whose command text merely mentioned those - and
    killed the terminal. Killing by substring is not a search, it is a
    guess, and this one gets to call proc.kill().

    So: the arguments must be exactly -m src.ui <http...>, the
    executable must be a python, and it must not be us.
    """
    if sys.platform != "win32":
        return 0
    try:
        import psutil
    except ImportError:
        return 0

    killed = 0
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.pid == os.getpid():
                continue
            argv = list(proc.info.get("cmdline") or [])
            if len(argv) != 4:
                continue
            if not Path(argv[0]).name.lower().startswith("python"):
                continue
            if argv[1] != "-m" or argv[2] != "src.ui":
                continue
            if not argv[3].startswith("http://127.0.0.1"):
                continue
            proc.kill()
            killed += 1
        except Exception:  # noqa: BLE001
            continue
    return killed


def close_interface() -> dict:
    """Shut the window process down entirely, not merely hide it."""
    global _process
    with _lock:
        if _process is None or _process.poll() is not None:
            return {"status": "success", "what": "was not open"}
        try:
            _process.terminate()
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": str(exc)}
        _process = None
    return {"status": "success", "what": "closed"}
