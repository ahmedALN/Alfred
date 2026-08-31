"""Being asked to put something back."""

from __future__ import annotations

from typing import Any

from src.brain.undo import Undo
from src.tools.base import AlfredTool


class UndoTool(AlfredTool):
    name = "undo"

    description = (
        "Put back something Alfred just did. Use for 'undo that', "
        "'no, put it back', 'close what you opened', 'never mind'. "
        "action=what_can_i_undo lists what is still reversible. Only "
        "some things can be - opening an app, mostly - and anything "
        "that cannot be is said plainly rather than half-tried."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["undo_last", "what_can_i_undo"],
                },
                "id": {
                    "type": "string",
                    "description": "A particular one, from the list.",
                },
            },
        }

    def __init__(self, undo: Undo, registry: Any = None) -> None:
        self._undo = undo
        self._registry = registry

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action") or "undo_last").strip().lower()

        if action == "what_can_i_undo":
            found = self._undo.recent()
            return {
                "status": "success",
                "count": len(found),
                "can_undo": [
                    {"id": r["id"], "what": r["what"],
                     "reversible": bool(r["tool"])}
                    for r in found
                ],
            }

        entry_id = str(arguments.get("id") or "").strip()
        entry = None
        if entry_id:
            entry = next(
                (r for r in self._undo.recent(20) if r["id"] == entry_id), None
            )
        else:
            entry = self._undo.last()

        if entry is None:
            return {
                "status": "not_found",
                "error": (
                    "nothing recent that Alfred knows how to put back"
                ),
            }

        if not entry["tool"]:
            # Honest about the ones it cannot reverse, rather than
            # pretending or half-trying.
            self._undo.mark(entry["id"])
            return {
                "status": "cannot",
                "what": entry["what"],
                "error": (
                    f"Alfred {entry['what']} and cannot put that back "
                    "itself. Tell the user what to do."
                ),
            }

        if self._registry is None:
            return {"status": "error", "error": "no tools to undo with"}

        import json

        try:
            result = self._registry.execute(
                entry["tool"], json.loads(entry["args"] or "{}")
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

        from src.tools.results import tool_succeeded

        if tool_succeeded(result):
            self._undo.mark(entry["id"])
            return {"status": "success", "undid": entry["what"],
                    "result": result}

        return {
            "status": "error",
            "what": entry["what"],
            "error": "tried to put it back and could not",
            "result": result,
        }
