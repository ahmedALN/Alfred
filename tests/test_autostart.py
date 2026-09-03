"""Alfred surviving a reboot.

It did not. The scheduled task it was written to install needs
administrator rights, so install() had been failing with "Access is
denied" - and nothing checked, so an assistant meant to be always on was
off after every restart until somebody started it by hand.
"""


import src.autostart as autostart


def _redirect(monkeypatch, tmp_path):
    """Point the Startup folder somewhere harmless."""
    monkeypatch.setattr(autostart, "_startup_folder", lambda: tmp_path)
    return tmp_path / autostart.SHORTCUT_NAME


def test_it_falls_back_when_the_task_needs_admin(monkeypatch, tmp_path):
    """Alfred does not have administrator rights and is not going to
    ask for them, so a refusal must not be the end of it."""
    target = _redirect(monkeypatch, tmp_path)
    monkeypatch.setattr(
        autostart, "_install_task",
        lambda: {"status": "error", "error": "ERROR: Access is denied."},
    )

    answer = autostart.install()

    assert answer["status"] == "installed"
    assert answer["how"] == "startup folder"
    assert target.exists()
    assert "elevated" in answer["note"]


def test_the_tidier_route_is_still_preferred(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    monkeypatch.setattr(
        autostart, "_install_task",
        lambda: {"status": "installed", "task": "AlfredAssistant"},
    )

    assert autostart.install()["task"] == "AlfredAssistant"


def test_what_it_writes_starts_alfred_without_a_console(monkeypatch, tmp_path):
    """A batch file would work and would flash a black box across the
    screen at every logon."""
    target = _redirect(monkeypatch, tmp_path)
    monkeypatch.setattr(autostart, "_install_task", lambda: {"status": "error"})

    autostart.install()
    script = target.read_text(encoding="utf-8")

    assert "src.watchdog" in script
    assert script.rstrip().endswith("0, False")     # hidden window
    assert "pythonw" in script


def test_it_can_be_taken_out_again(monkeypatch, tmp_path):
    target = _redirect(monkeypatch, tmp_path)
    monkeypatch.setattr(autostart, "_install_task", lambda: {"status": "error"})
    autostart.install()

    answer = autostart.uninstall()

    assert "startup folder" in answer["removed"]
    assert not target.exists()


def test_removing_what_is_not_there_says_so(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)

    assert autostart.uninstall()["status"] == "not_installed"


def test_status_says_which_way_it_is_set_up(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    monkeypatch.setattr(autostart, "_install_task", lambda: {"status": "error"})

    assert autostart.status()["status"] == "not_installed"
    autostart.install()
    assert autostart.status()["how"] == ["startup folder"]


# ====================================================================
# The scheduled task itself must not route through cmd.exe.
#
# A raw `cmd /c "cd /d ... && pythonw ..."` needs cmd to set the
# working directory, and cmd.exe always owns a console - one Task
# Scheduler has no setting to hide. Worse, cmd was WAITING on pythonw
# (nothing backgrounded it), so it was not a flash, it was a visible
# window for as long as Alfred ran, every boot.
# ====================================================================


def test_the_scheduled_task_never_routes_through_cmd(monkeypatch, tmp_path):
    monkeypatch.setattr(autostart, "_startup_folder", lambda: tmp_path)

    calls = []

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append(args)
        return _Result()

    monkeypatch.setattr(autostart.subprocess, "run", fake_run)

    result = autostart._install_task()

    assert result["status"] == "installed"
    tr_index = calls[0].index("/TR") + 1
    tr_value = calls[0][tr_index]
    assert "cmd" not in tr_value.lower()
    assert tr_value.lower().startswith("wscript.exe")


def test_the_scheduled_task_points_at_a_hidden_vbs_launcher(monkeypatch, tmp_path):
    monkeypatch.setattr(autostart, "_startup_folder", lambda: tmp_path)

    calls = []

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append(args)
        return _Result()

    monkeypatch.setattr(autostart.subprocess, "run", fake_run)

    autostart._install_task()

    vbs_path = tmp_path / autostart.SHORTCUT_NAME
    assert vbs_path.exists()  # written before schtasks was ever invoked

    tr_index = calls[0].index("/TR") + 1
    assert str(vbs_path) in calls[0][tr_index]

    script = vbs_path.read_text(encoding="utf-8")
    assert script.rstrip().endswith("0, False")  # hidden window
    assert "pythonw" in script


def test_a_failed_task_creation_still_reports_the_real_error(monkeypatch, tmp_path):
    monkeypatch.setattr(autostart, "_startup_folder", lambda: tmp_path)

    class _Result:
        returncode = 1
        stdout = ""
        stderr = "ERROR: Access is denied."

    monkeypatch.setattr(autostart.subprocess, "run", lambda *a, **k: _Result())

    result = autostart._install_task()

    assert result["status"] == "error"
    assert "denied" in result["error"].lower()
