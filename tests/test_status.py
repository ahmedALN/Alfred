import sqlite3
from datetime import UTC

from src import status


def test_audit_summary_reads_counts(tmp_path):
    db = tmp_path / "audit.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE brain_audit (id INTEGER PRIMARY KEY, session_id TEXT, "
        "kind TEXT, payload TEXT, created_at TEXT);"
    )
    from datetime import datetime

    today = datetime.now(UTC).date().isoformat()
    conn.executemany(
        "INSERT INTO brain_audit (kind, payload, created_at) VALUES (?, ?, ?)",
        [
            ("tick", "{}", f"{today}T10:00:00"),
            ("tick", "{}", f"{today}T10:01:00"),
            ("spoken", '{"text": "disk is low"}', f"{today}T10:02:00"),
            ("spoken", '{"suppressed": true, "text": "x"}', f"{today}T10:03:00"),
        ],
    )
    conn.commit()
    conn.close()

    out = status._audit_summary(db)
    assert out["available"] is True
    assert out["today"]["tick"] == 2
    assert out["today"]["spoken"] == 2
    assert out["recent_spoken"] == ["disk is low"]  # suppressed one excluded


def test_audit_summary_missing_db(tmp_path):
    assert status._audit_summary(tmp_path / "nope.sqlite3") == {"available": False}


def test_fact_count(tmp_path):
    db = tmp_path / "mem.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, content TEXT)")
    conn.executemany(
        "INSERT INTO facts (content) VALUES (?)", [("a",), ("b",), ("c",)]
    )
    conn.commit()
    conn.close()
    assert status._fact_count(db) == 3
    assert status._fact_count(tmp_path / "nope.sqlite3") is None


def test_main_runs_clean(capsys):
    rc = status.main()
    assert rc == 0
    assert "Alfred status" in capsys.readouterr().out
