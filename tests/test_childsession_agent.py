"""ChildInputAgent.exe is a real console app - OutputType is Exe, not
WinExe, and it writes to Console throughout for its own diagnostics -
so the startup entry that used to register it directly under Run
would open a visible console window at every logon. That was also,
independently, exactly what a stray scheduled task set up outside
this codebase (not by any of this file's code) was already doing -
found by reading the actual live task's Action and Settings.Hidden,
not guessed.

The fix routes the same WScript.Shell.Run(..., 0, False) hidden-window
technique src/autostart.py already uses for Alfred itself through the
Run-key entry too - deliberately with NO cmd.exe wrapper and no '>>
log' redirect. That exact combination was tried once already
(scripts/install-child-agent-task.ps1's own comments, and
windows/native/ChildInputAgent/Logging.cs) and broke isolation: two
agents is the normal case, one in the user's session and one in
Alfred's, and whichever started first held the shared log file - the
second's redirect failed, cmd exited 1, no agent started. The agent
now keeps its own per-session log file for exactly that reason, so
this launcher's only job is opening it hidden.
"""

from __future__ import annotations

import os
from pathlib import Path

import src.childsession as childsession


def test_write_agent_launcher_produces_a_hidden_vbs(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    target = childsession._write_agent_launcher(r"C:\fake\ChildInputAgent.exe")
    script = Path(target).read_text(encoding="utf-8")

    assert target.endswith("launch_child_agent.vbs")
    assert os.path.exists(target)
    assert 'sh.Run """C:\\fake\\ChildInputAgent.exe"""' in script
    assert script.rstrip().endswith("0, False")  # hidden window style


def test_write_agent_launcher_never_wraps_in_cmd_or_redirects_a_log(tmp_path, monkeypatch):
    """That exact combination is what broke isolation before - see the
    module docstring. The agent owns its own per-session log now; a
    shell wrapper here has no job left to do except reintroduce that
    bug."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    target = childsession._write_agent_launcher(r"C:\fake\ChildInputAgent.exe")
    script = Path(target).read_text(encoding="utf-8")

    assert "cmd" not in script.lower()
    assert ">>" not in script
    assert "2>&1" not in script


def test_write_agent_launcher_quotes_survive_a_path_with_spaces(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    exe = r"C:\Program Files\Alfred Agent\ChildInputAgent.exe"
    target = childsession._write_agent_launcher(exe)
    script = Path(target).read_text(encoding="utf-8")

    assert f'""{exe}""' in script


def test_write_agent_launcher_overwrites_on_a_second_call(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    childsession._write_agent_launcher(r"C:\old\Agent.exe")
    target = childsession._write_agent_launcher(r"C:\new\Agent.exe")
    script = Path(target).read_text(encoding="utf-8")

    assert "old" not in script
    assert "new" in script


# ====================================================================
# cmd_install_agent
# ====================================================================


def test_install_agent_reports_when_the_exe_is_not_built(monkeypatch, capsys):
    monkeypatch.setattr(childsession, "_agent_exe", lambda: None)

    assert childsession.cmd_install_agent([]) == 1
    assert "dotnet build" in capsys.readouterr().out


def test_install_agent_routes_through_wscript_not_the_bare_console_exe(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(
        childsession, "_agent_exe", lambda: r"C:\fake\ChildInputAgent.exe"
    )

    seen = {}

    def fake_ps(command, timeout=20.0):
        seen["command"] = command
        # cmd_install_agent looks for the vbs path in what _ps returns
        # to decide whether it worked - echo it back like the real
        # Get-ItemProperty read would.
        vbs = str(tmp_path / "Alfred" / "launch_child_agent.vbs")
        return f'wscript.exe //B "{vbs}"'

    monkeypatch.setattr(childsession, "_ps", fake_ps)

    result = childsession.cmd_install_agent([])

    assert result == 0
    assert "wscript.exe" in seen["command"]
    assert "ChildInputAgent.exe" not in seen["command"]  # never the bare exe


def test_install_agent_reports_honestly_when_it_cannot_confirm(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(
        childsession, "_agent_exe", lambda: r"C:\fake\ChildInputAgent.exe"
    )
    monkeypatch.setattr(childsession, "_ps", lambda command, timeout=20.0: "")

    assert childsession.cmd_install_agent([]) == 1
