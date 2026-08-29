from __future__ import annotations

import subprocess
import time
from pathlib import Path

from src.windows.child_session import ChildSessionClient, ChildSessionError

_AGENT_DIR = (
    Path(__file__).resolve().parent.parent
    / "native"
    / "ChildInputAgent"
    / "bin"
    / "Release"
)


def _find_agent_exe() -> Path | None:
    if not _AGENT_DIR.exists():
        return None

    for candidate in _AGENT_DIR.rglob("ChildInputAgent.exe"):
        return candidate

    return None


def ensure_agent_running(
    *,
    launch: bool = True,
    wait_seconds: float = 6.0,
) -> dict[str, object]:
    """
    Make sure the ChildInputAgent (screen capture + input for Alfred's
    controlled desktop) is reachable.

    Returns a status dict; never raises. If the agent is already
    listening on its pipe, does nothing. Otherwise, when ``launch`` is
    set and the built exe exists, starts it detached and waits briefly
    for the pipe to come up.

    Note: this launches the agent in Alfred's *current* Windows session.
    Running it inside an isolated child session (true "does not disturb
    you" isolation) is a separate deployment step - start the same exe
    from within Session 2.
    """

    probe = ChildSessionClient()

    try:
        probe.connect()
        session = probe.session()
        probe.close()
        return {"status": "already_running", "session": session}
    except ChildSessionError:
        probe.close()

    if not launch:
        return {"status": "unavailable", "reason": "agent not running"}

    exe = _find_agent_exe()

    if exe is None:
        return {
            "status": "unavailable",
            "reason": (
                "ChildInputAgent.exe not built. Run: dotnet build "
                "src/windows/native/ChildInputAgent -c Release"
            ),
        }

    try:
        subprocess.Popen(
            [str(exe)],
            cwd=str(exe.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        return {"status": "error", "reason": f"could not launch agent: {exc}"}

    deadline = time.monotonic() + wait_seconds

    while time.monotonic() < deadline:
        time.sleep(0.4)
        client = ChildSessionClient()
        try:
            client.connect()
            session = client.session()
            client.close()
            return {"status": "launched", "session": session, "exe": str(exe)}
        except ChildSessionError:
            client.close()

    return {
        "status": "error",
        "reason": "agent launched but pipe did not come up in time",
    }
