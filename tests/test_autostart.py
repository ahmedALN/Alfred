import subprocess
from types import SimpleNamespace

from src import autostart


def _fake_run(monkeypatch, returncode=0, stdout="", stderr=""):
    calls = []

    def run(args, capture_output=True, text=True):
        calls.append(args)
        return SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=stderr
        )

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_install_creates_logon_task(monkeypatch):
    calls = _fake_run(monkeypatch)
    result = autostart.install()

    assert result["status"] == "installed"
    args = calls[0]
    assert args[:4] == ["schtasks", "/Create", "/TN", "AlfredAssistant"]
    assert "ONLOGON" in args
    assert "-m src.main" in " ".join(args)
    assert "/RL" in args and "LIMITED" in args  # no admin required


def test_install_surfaces_schtasks_error(monkeypatch):
    _fake_run(monkeypatch, returncode=1, stderr="Access is denied.")
    result = autostart.install()
    assert result["status"] == "error"
    assert "denied" in result["error"]


def test_uninstall_deletes_task(monkeypatch):
    calls = _fake_run(monkeypatch)
    result = autostart.uninstall()
    assert result["status"] == "removed"
    assert calls[0] == [
        "schtasks", "/Delete", "/TN", "AlfredAssistant", "/F"
    ]


def test_status_reports_installed(monkeypatch):
    _fake_run(monkeypatch, returncode=0, stdout="AlfredAssistant  Ready")
    assert autostart.status()["status"] == "enabled"


def test_status_reports_missing(monkeypatch):
    _fake_run(monkeypatch, returncode=1, stderr="ERROR: cannot find the file")
    assert autostart.status()["status"] == "not_installed"
