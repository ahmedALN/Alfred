from __future__ import annotations

from typing import Any

# Statuses that mean "this did not achieve anything", regardless of which
# key a given tool happens to use.
_FAILURE_STATUSES = {"error", "not_found", "refused", "failed", "denied"}


def tool_succeeded(result: Any) -> bool:
    """
    One place that decides whether a tool result represents real progress.

    Alfred's tools are not perfectly consistent - PowerShell returns
    ``success: False``, the launcher returns ``status: "not_found"``,
    others return ``status: "error"``. Both the task agent and the voice
    tool-call handler must judge these the same way, or Alfred ends up
    reporting failed steps as done (the "it opened Drake" class of bug).
    """

    if not isinstance(result, dict):
        return False

    status = str(result.get("status", "")).lower()
    if status in _FAILURE_STATUSES:
        return False

    if result.get("success") is False:
        return False

    rc = result.get("return_code")
    if isinstance(rc, bool):
        rc = None
    if isinstance(rc, int) and rc != 0:
        return False

    # An 'error' field alongside anything other than an explicit success.
    if result.get("error") and status != "success":
        return False

    return True


def summarize_result(result: Any, limit: int = 600) -> str:
    """Compact, model-friendly one-liner for a tool result."""
    import json

    try:
        text = result if isinstance(result, str) else json.dumps(
            result, default=str, ensure_ascii=False
        )
    except Exception:  # noqa: BLE001
        text = str(result)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
