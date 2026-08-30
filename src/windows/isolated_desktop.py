from __future__ import annotations

import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

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

    def __init__(
        self,
        host_exe: Path | None = None,
        client_provider: Callable[[], ChildSessionClient] | None = None,
    ) -> None:
        self._host_exe = host_exe or _HOST_EXE
        # The agent in the session accepts ONE connection at a time, so
        # everything here shares the router's rather than opening a
        # second one and being refused as ERROR_PIPE_BUSY.
        self._client_provider = client_provider
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

            self._recycle_if_stale()

            if self._host is None or self._host.poll() is not None:
                self._reap_orphan_hosts()
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

    def _reap_orphan_hosts(self) -> None:
        """Close host windows left over from previous runs.

        Each host holds a session open and sits in the window list as
        "Alfred Child Session (Disconnected)". They are harmless one at
        a time and absurd fourteen at a time, which is what a day of
        starting and recycling sessions produces.
        """
        mine = self._host.pid if self._host is not None else None

        try:
            listing = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq ChildSessionProbe.exe",
                 "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return

        pids: list[int] = []
        for line in listing.splitlines():
            parts = [p.strip('" ') for p in line.split('","')]
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[1])
            except ValueError:
                continue
            if pid != mine:
                pids.append(pid)

        if not pids:
            return

        for pid in pids:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True, timeout=10,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.SubprocessError):
                continue

        print(f"[Isolated] cleared {len(pids)} leftover session host(s).")

    def _recycle_if_stale(self) -> None:
        """Log off a leftover session whose agent is never coming back.

        The agent starts from a logon trigger, so a session that is
        logged on but has no agent - Alfred was killed, the agent
        crashed, the machine was left mid-task - can never heal itself:
        the logon it needed already happened. Waiting the full timeout
        and giving up leaves isolation permanently broken until the user
        reboots. Logging the session off means the next one starts
        clean.
        """
        stale = self.session_id

        if stale is None:
            return

        if self._host is not None and self._host.poll() is None:
            # Our own host is up; the agent is simply still starting.
            return

        # It may be a session we are racing rather than a dead one.
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if self._agent_ready():
                return
            time.sleep(1.0)

        print(f"[Isolated] session {stale} has no agent - recycling it.")

        try:
            subprocess.run(
                ["logoff", str(stale)],
                check=False,
                timeout=20,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"[Isolated] could not log off session {stale}: {exc}")
            return

        # The id only clears once the logoff completes.
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if self.session_id is None:
                break
            time.sleep(0.5)

        self._baseline = set()
        self._launched.clear()

    def _agent_ready(self) -> bool:
        if self.session_id is None:
            return False
        try:
            with self._session_client() as client:
                client.session()
            return True
        except ChildSessionError:
            return False

    # --------------------------------------------------------------- acting

    def client(self) -> ChildSessionClient:
        """A connected client for the isolated session."""
        if self._client_provider is not None:
            return self._client_provider()

        client = ChildSessionClient("child")
        client.connect()
        return client

    @contextmanager
    def _session_client(self) -> Iterator[ChildSessionClient]:
        """A client for the duration of one call.

        A borrowed connection is left open - it belongs to the router,
        and closing it would break the very next call.
        """
        client = self.client()
        try:
            yield client
        finally:
            if self._client_provider is None:
                client.close()

    def launch(self, path: str, args: str | None = None) -> dict[str, Any]:
        """Open an app inside the isolated session, remembering the pid so
        it can be cleaned up when the task finishes."""
        with self._session_client() as client:
            result = client.launch(path, args)

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
        with self._session_client() as client:
            return client.list_apps()

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

        targets = []
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
            if self._client_provider is None:
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
