import json

from src.usage import UsageTracker, record_response


def test_records_requests_and_tokens(tmp_path):
    t = UsageTracker(tmp_path / "u.json")
    t.record(100, 40)
    t.record(50, 10)
    day = t.today()
    assert day["requests"] == 2
    assert day["input_tokens"] == 150
    assert day["output_tokens"] == 50


def test_records_errors(tmp_path):
    t = UsageTracker(tmp_path / "u.json")
    t.record_error("quota")
    t.record_error("quota")
    t.record_error("disconnect")
    assert t.today()["errors"] == {"quota": 2, "disconnect": 1}


def test_persists_across_instances(tmp_path):
    path = tmp_path / "u.json"
    UsageTracker(path).record(10, 5)
    assert UsageTracker(path).today()["requests"] == 1


def test_survives_corrupt_file(tmp_path):
    path = tmp_path / "u.json"
    path.write_text("{ not json")
    t = UsageTracker(path)
    t.record(1, 1)  # should not raise
    assert t.today()["requests"] == 1


def test_record_response_reads_usage_metadata(tmp_path, monkeypatch):
    import src.usage as usage_mod

    tracker = UsageTracker(tmp_path / "u.json")
    monkeypatch.setattr(usage_mod, "USAGE", tracker)

    class Meta:
        prompt_token_count = 123
        candidates_token_count = 45

    class Resp:
        usage_metadata = Meta()

    record_response(Resp())
    day = tracker.today()
    assert day["input_tokens"] == 123 and day["output_tokens"] == 45


def test_record_response_without_metadata_still_counts(tmp_path, monkeypatch):
    import src.usage as usage_mod

    tracker = UsageTracker(tmp_path / "u.json")
    monkeypatch.setattr(usage_mod, "USAGE", tracker)
    record_response(object())
    assert tracker.today()["requests"] == 1
