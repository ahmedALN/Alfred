from src import setup


def test_ask_non_tty_returns_default(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert setup._ask("x?", default=True) is True
    assert setup._ask("x?", default=False) is False


def test_check_python_deps_reports_ok(capsys, monkeypatch):
    # everything importable in the test env
    setup.check_python_deps()
    out = capsys.readouterr().out
    assert "Python dependencies" in out


def test_main_runs(capsys, monkeypatch):
    # keep it from shelling out / prompting
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(setup, "check_ollama", lambda: None)
    monkeypatch.setattr(setup, "build_native", lambda: None)
    monkeypatch.setattr(setup, "ensure_env", lambda: None)
    monkeypatch.setattr(setup, "offer_extras", lambda: None)

    assert setup.main() == 0
    assert "python -m src.main" in capsys.readouterr().out
