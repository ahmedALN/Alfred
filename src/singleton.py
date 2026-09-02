from __future__ import annotations

import atexit
import os
import tempfile
from pathlib import Path

import psutil


class AlreadyRunning(RuntimeError):
    """Raised when another Alfred instance already holds the lock."""

    def __init__(self, pid: int) -> None:
        super().__init__(
            f"Alfred is already running (pid {pid}). "
            "Close that instance first, or use its tray icon to quit."
        )
        self.pid = pid


class SingleInstance:
    """
    A PID lockfile guard so only one Alfred runs at a time. Two voice
    sessions playing at once is the classic "why are there two voices"
    bug.

    Stale locks (process gone, or the pid was recycled by something
    unrelated) are reclaimed automatically.
    """

    def __init__(self, name: str = "alfred", marker: str = "src.main") -> None:
        self._path = Path(tempfile.gettempdir()) / f"{name}.lock"
        self._marker = marker
        self._locked = False

    # ----------------------------------------------------------------

    def _holder_is_alive(self) -> int | None:
        try:
            pid = int(self._path.read_text().strip())
        except (OSError, ValueError):
            return None

        if pid == os.getpid():
            return None

        if not psutil.pid_exists(pid):
            return None

        try:
            proc = psutil.Process(pid)
            cmdline = " ".join(proc.cmdline()).lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Can't inspect it - assume it's not us to avoid a deadlock.
            return None

        if self._marker in cmdline or "alfred" in cmdline:
            return pid

        return None

    def acquire(self) -> None:
        # Two attempts: the second only after clearing a confirmed-stale
        # lock. Uses an atomic O_EXCL create so two instances racing to
        # start can't both win (the "two voices" bug).
        for attempt in range(2):
            try:
                fd = os.open(
                    self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
                )
            except FileExistsError:
                holder = self._holder_is_alive()
                if holder is not None:
                    raise AlreadyRunning(holder)  # noqa: B904
                # Stale lock (dead pid / unrelated process). Clear once.
                if attempt == 0:
                    try:
                        self._path.unlink()
                    except OSError:
                        pass
                    continue
                raise AlreadyRunning(-1)  # noqa: B904
            except OSError as exc:
                print(f"[Singleton] could not create lock file: {exc}")
                return

            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(str(os.getpid()))
            self._locked = True
            atexit.register(self.release)
            return

    def release(self) -> None:
        if not self._locked:
            return

        self._locked = False

        try:
            if self._path.exists():
                current = self._path.read_text().strip()
                if current == str(os.getpid()):
                    self._path.unlink()
        except OSError:
            pass

    def __enter__(self) -> SingleInstance:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
