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


# How much of any one string a model needs before it is just noise.
_ROOM = 500


def for_model(result: Any) -> dict[str, Any]:
    """A tool result the model cannot misread.

    Alfred once told the user a screenshot had been saved when the step
    had failed with return code 1. The verdict was right there in the
    dict - success: False - and it still got it wrong, because
    PowerShell echoes the whole failing script back inside stderr, and
    that script contained the line Write-Output "Image saved to $Path".
    The model read its own script's success message and believed it.

    So the verdict goes first, in words, and the echo is cut down to the
    part that says what went wrong. A model that has to hunt for the
    outcome will eventually hunt wrong.
    """
    if not isinstance(result, dict):
        return {"outcome": "FAILED", "detail": _trim(str(result))}

    ok = tool_succeeded(result)
    shown: dict[str, Any] = {"outcome": "SUCCESS" if ok else "FAILED"}

    if not ok:
        shown["note"] = (
            "This step FAILED. Do not tell the user it worked. Either "
            "try a different way or say plainly that it did not work."
        )

    command = str(result.get("command") or "")
    for key, value in result.items():
        if key in ("outcome", "note"):
            continue
        if isinstance(value, str):
            shown[key] = _trim(_without_echo(value, command))
        else:
            shown[key] = value

    return shown


def _without_echo(text: str, command: str) -> str:
    """Drop the command a shell repeats back before its error.

    It is the largest part of the message and the least informative, and
    when the command contains its own success message it is worse than
    uninformative.
    """
    command = command.strip()
    if len(command) < 40 or command not in text:
        return text
    return text.replace(command, "").lstrip(" :\r\n") or text


def _trim(text: str, limit: int = _ROOM) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
