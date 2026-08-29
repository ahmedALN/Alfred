import json

import pytest

from src import memory_cli
from src.memory.store import MemoryStore


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "mem.sqlite3"
    monkeypatch.setattr(memory_cli, "_DB", path)
    s = MemoryStore(path)
    s.add_fact("User prefers dark mode.", category="preference")
    s.add_fact("Firewall blocks port 3389.", category="system")
    s.close()
    return path


def test_list(db, capsys):
    assert memory_cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "dark mode" in out and "port 3389" in out


def test_search(db, capsys):
    memory_cli.main(["search", "firewall"])
    out = capsys.readouterr().out
    assert "3389" in out and "dark mode" not in out


def test_forget(db, capsys):
    memory_cli.main(["forget", "1"])
    s = MemoryStore(db)
    remaining = [f.content for f in s.all_facts()]
    s.close()
    assert "User prefers dark mode." not in remaining


def test_edit(db):
    memory_cli.main(["edit", "2", "Firewall blocks RDP entirely."])
    s = MemoryStore(db)
    contents = [f.content for f in s.all_facts()]
    s.close()
    assert "Firewall blocks RDP entirely." in contents


def test_export(db, tmp_path, capsys):
    out = tmp_path / "dump.json"
    memory_cli.main(["export", str(out)])
    data = json.loads(out.read_text())
    assert len(data) == 2
    assert {d["category"] for d in data} == {"preference", "system"}
