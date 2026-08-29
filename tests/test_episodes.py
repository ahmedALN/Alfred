from src.memory.episodes import EpisodeStore


def test_record_and_recent(tmp_path):
    s = EpisodeStore(tmp_path / "ep.sqlite3")
    s.record("task", "you asked: tidy downloads", outcome="done")
    s.record("proactive", "Disk C is nearly full.")
    rows = s.recent(hours=1)
    assert [r["summary"] for r in rows] == [
        "Disk C is nearly full.",
        "you asked: tidy downloads",
    ]
    assert rows[1]["outcome"] == "done"
    s.close()


def test_empty_summary_is_ignored(tmp_path):
    s = EpisodeStore(tmp_path / "ep.sqlite3")
    assert s.record("task", "   ") == 0
    assert s.recent() == []
    s.close()


def test_search(tmp_path):
    s = EpisodeStore(tmp_path / "ep.sqlite3")
    s.record("task", "moved report files to archive", outcome="done")
    s.record("task", "opened spotify", outcome="done")
    hits = s.search("report")
    assert len(hits) == 1 and "report" in hits[0]["summary"]
    assert s.search("nonsense") == []
    s.close()


def test_prune(tmp_path):
    s = EpisodeStore(tmp_path / "ep.sqlite3")
    rid = s.record("task", "old thing")
    # backdate it
    s._conn.execute(
        "UPDATE episodes SET at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
        (rid,),
    )
    s._conn.commit()
    assert s.prune(keep_days=30) == 1
    assert s.recent(hours=99999) == []
    s.close()


def test_cli_recent_smoke(tmp_path, capsys, monkeypatch):
    db = tmp_path / "ep.sqlite3"
    monkeypatch.setenv("ALFRED_EPISODE_DB", str(db))
    import importlib

    import src.episodes as mod
    importlib.reload(mod)
    mod.main(["recent"])
    out = capsys.readouterr().out
    assert "episode(s)" in out
