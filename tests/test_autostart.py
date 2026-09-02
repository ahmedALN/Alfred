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
