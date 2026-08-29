import os

import pytest

from src.singleton import AlreadyRunning, SingleInstance


def test_acquire_writes_lock(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    s = SingleInstance("alfred-test")
    s.acquire()
    assert (tmp_path / "alfred-test.lock").read_text().strip() == str(os.getpid())
    s.release()
    assert not (tmp_path / "alfred-test.lock").exists()


def test_second_instance_is_blocked_by_a_live_holder(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

    lock = tmp_path / "alfred-test.lock"
    lock.write_text("4321")  # pretend another Alfred owns it

    # A live process whose cmdline looks like Alfred.
    class FakeProc:
        def cmdline(self):
            return ["python", "-m", "src.main"]

    monkeypatch.setattr("psutil.pid_exists", lambda p: p == 4321)
    monkeypatch.setattr("psutil.Process", lambda p: FakeProc())

    with pytest.raises(AlreadyRunning) as exc:
        SingleInstance("alfred-test").acquire()

    assert exc.value.pid == 4321


def test_stale_lock_is_reclaimed(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

    (tmp_path / "alfred-test.lock").write_text("999999")
    monkeypatch.setattr("psutil.pid_exists", lambda p: False)

    s = SingleInstance("alfred-test")
    s.acquire()  # should not raise
    assert (tmp_path / "alfred-test.lock").read_text().strip() == str(os.getpid())
    s.release()


def test_lock_held_by_unrelated_process_is_reclaimed(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    (tmp_path / "alfred-test.lock").write_text("4321")

    class OtherProc:
        def cmdline(self):
            return ["chrome.exe", "--type=renderer"]

    monkeypatch.setattr("psutil.pid_exists", lambda p: True)
    monkeypatch.setattr("psutil.Process", lambda p: OtherProc())

    SingleInstance("alfred-test").acquire()  # pid recycled -> not us -> ok
