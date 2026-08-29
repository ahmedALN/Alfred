import json
from pathlib import Path

import pytest

from src.brain.audit import AuditLog


@pytest.fixture()
def audit(tmp_path: Path):
    log = AuditLog(tmp_path / "brain_audit.sqlite3")
    yield log
    log.close()


def test_record_and_read_back(audit: AuditLog):
    row_id = audit.record("notable", {"summary": "disk C: low", "severity": "warn"})

    assert row_id == 1

    recent = audit.recent()

    assert len(recent) == 1
    assert recent[0]["kind"] == "notable"
    assert recent[0]["payload"]["summary"] == "disk C: low"


def test_jsonl_mirror_written(audit: AuditLog, tmp_path: Path):
    audit.record("tick", {"tick": 1})
    audit.record("spoken", {"text": "hello"})

    mirror = tmp_path / "brain_audit.jsonl"

    assert mirror.exists()

    lines = [json.loads(line) for line in mirror.read_text().splitlines()]

    assert [entry["kind"] for entry in lines] == ["tick", "spoken"]
    assert lines[1]["text"] == "hello"


def test_recent_is_newest_first(audit: AuditLog):
    for i in range(5):
        audit.record("tick", {"tick": i})

    recent = audit.recent(limit=3)

    assert [r["payload"]["tick"] for r in recent] == [4, 3, 2]
