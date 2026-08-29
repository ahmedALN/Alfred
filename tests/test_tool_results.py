from src.tools.results import summarize_result, tool_succeeded


def test_powershell_failure_is_not_success():
    assert tool_succeeded({"success": False, "return_code": 1, "stderr": "boom"}) is False
    assert tool_succeeded({"status": "success", "success": True, "return_code": 0}) is True


def test_not_found_is_not_success():
    assert tool_succeeded({"status": "not_found", "note": "no such app"}) is False


def test_status_error_is_not_success():
    assert tool_succeeded({"status": "error", "error": "x"}) is False
    assert tool_succeeded({"status": "success", "opened": "spotify"}) is True


def test_nondict_is_not_success():
    assert tool_succeeded("ok") is False
    assert tool_succeeded(None) is False


def test_error_field_without_success_status():
    assert tool_succeeded({"error": "partial failure"}) is False
    assert tool_succeeded({"status": "success", "error": ""}) is True


def test_summarize_result_caps_length():
    big = {"data": "x" * 5000}
    out = summarize_result(big, limit=200)
    assert len(out) <= 200
