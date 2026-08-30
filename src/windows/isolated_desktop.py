from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from src.windows.child_session import (
    ChildSessionClient,
    ChildSessionError,
    child_session_id,
)

_HOST_EXE = (
    Path(__file__).resolve().parent
    / "native" / "ChildSessionProbe" / "bin" / "Release" / "net48"
    / "ChildSessionProbe.exe"
)


class IsolatedDesktop:
    """
    Alfred's own Windows session - a place to open apps and click without
    touching the user's screen.

    On-demand by design: the session is created when a task actually asks
    for isolation and torn down afterwards, rather than sitting there
    consuming a desktop's worth of RAM all day.

    Everything here degrades rather than raises. If the session cannot be
    created, callers fall back to the user's desktop, which is a worse
    experience but not a failure.
    """

    def __init__(self, host_exe: Path | None = None) -> None:
        self._host_exe = host_exe or _HOST_EXE
        self._host: subprocess.Popen[bytes] | None = None
        self._lock = threading.RLock()
        # Apps Alfred started in there, so it can clean up after itself.
        self._launched: list[int] = []
        # Everything already open when Alfred arrived. Cleanup closes what
        # appeared AFTER this - tracking launched pids alone is not enough,
        # because single-instance apps (Notepad, Store apps) hand off to an
        # existing process and the pid we were given exits immediately.
        self._baseline: set[int] = set()

    # ---------------------------------------------------------------- state

    @property
    def available(self) -> bool:
        return self._host_exe.exists()

    @property
    def session_id(self) -> int | None:
        return child_session_id()

    @property
    def running(self) -> bool:
        return self.session_id is not None

    # -------------------------------------------------------------- startup

    @staticmethod
    def _foreground() -> int:
        import ctypes

        try:
            return int(ctypes.windll.user32.GetForegroundWindow())
        except Exception:  # noqa: BLE001
            return 0

    @staticmethod
    def _restore_foreground(hwnd: int) -> None:
        """Put the user's window back in front.

        The RDP host window is created off-screen and marked no-activate,
        but the ActiveX control still grabs focus while connecting. This
        is the backstop: whatever the user was doing stays in front.
        """
        if not hwnd:
            return
        import ctypes

        try:
            u = ctypes.windll.user32
            if u.GetForegroundWindow() != hwnd:
                u.SetForegroundWindow(hwnd)
        except Exception:  # noqa: BLE001
            pass

    def ensure(self, timeout: float = 60.0) -> int | None:
        """Make sure the isolated session exists and its agent is up.

        Returns the session id, or None if it could not be brought up.
        """
        user_window = self._foreground()
        try:
            return self._ensure_locked(timeout)
        finally:
            self._restore_foreground(user_window)

    def _ensure_locked(self, timeout: float) -> int | None:
        with self._lock:
            if self._agent_ready():
                if not self._baseline:
                    self._snapshot_baseline()
                return self.session_id

            if not self.available:
                print(
                    "[Isolated] host not built - run: dotnet build "
                    "src/windows/native/ChildSessionProbe -c Release"
                )
                return None

            if self._host is None or self._host.poll() is not None:
                try:
                    self._host = subprocess.Popen(
                        [str(self._host_exe)],
                        cwd=str(self._host_exe.parent),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(
                            subprocess, "CREATE_NO_WINDOW", 0
                        ),
                    )
                except OSError as exc:
                    print(f"[Isolated] could not start the host: {exc}")
                    return None

            # The session has to log on and its agent has to start; both
            # take a few seconds.
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self._agent_ready():
                    sid = self.session_id
                    self._snapshot_baseline()
                    print(f"[Isolated] session {sid} ready.")
                    return sid
                time.sleep(1.0)

            print("[Isolated] session did not become ready in time.")
            return None

    def _agent_ready(self) -> bool:
        if self.session_id is None:
            return False
        client = ChildSessionClient("child")
        try:
            client.connect()
            client.session()
            return True
        except ChildSessionError:
            return False
        finally:
            client.close()

    # --------------------------------------------------------------- acting

    def client(self) -> ChildSessionClient:
        """A connected client for the isolated session. Caller closes it."""
        client = ChildSessionClient("child")
        client.connect()
        return client

    def launch(self, path: str, args: str | None = None) -> dict[str, Any]:
        """Open an app inside the isolated session, remembering the pid so
        it can be cleaned up when the task finishes."""
        client = self.client()
        try:
            result = client.launch(path, args)
        finally:
            client.close()

        pid = result.get("pid")
        if isinstance(pid, int):
            with self._lock:
                self._launched.append(pid)
        return result

    def _snapshot_baseline(self) -> None:
        """Remember what was already open, so cleanup only closes ours."""
        try:
            self._baseline = {
                a["pid"] for a in self.apps() if isinstance(a.get("pid"), int)
            }
        except ChildSessionError:
            self._baseline = set()

    def apps(self) -> list[dict[str, Any]]:
        client = self.client()
        try:
            return client.list_apps()
        finally:
            client.close()

    # -------------------------------------------------------------- cleanup

    def cleanup(self, close_everything: bool = False) -> dict[str, Any]:
        """Close what Alfred opened in the isolated session.

        By default only the apps Alfred started itself. ``close_everything``
        also closes other windowed apps in there (the duplicated startup
        clutter), which is safe because nothing of the user's lives in
        that session.
        """
        with self._lock:
            mine = list(self._launched)
            baseline = set(self._baseline)
            self._launched.clear()

        if not self.running:
            return {"closed": [], "failed": [], "note": "no session running"}

        try:
            client = self.client()
        except ChildSessionError as exc:
            return {"closed": [], "failed": mine, "note": str(exc)}

        try:
            open_now = client.list_apps()
            targets: list[int] = []

            for app in open_now:
                pid = app.get("pid")
                if not isinstance(pid, int):
                    continue
                # Anything that appeared since Alfred started work is
                # Alfred's doing, whatever pid the launch reported.
                new_since_start = pid not in baseline
                if close_everything or new_since_start or pid in mine:
                    targets.append(pid)

            if not targets:
                return {"closed": [], "failed": [], "note": "nothing to close"}

            result = client.close_apps(targets)
            closed = result.get("closed", [])
            if closed:
                print(f"[Isolated] closed {len(closed)} app(s) in the session.")
            return result
        except ChildSessionError as exc:
            return {"closed": [], "failed": targets, "note": str(exc)}
        finally:
            client.close()

    def shutdown(self) -> None:
        """Tear the whole session down (also ends everything inside it)."""
        with self._lock:
            host, self._host = self._host, None
            self._launched.clear()

        if host is not None and host.poll() is None:
            try:
                host.terminate()
                host.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    host.kill()
                except Exception:  # noqa: BLE001
                    pass
