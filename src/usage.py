from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_FILE = _ROOT / "alfred_usage.json"


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


class UsageTracker:
    """
    Lightweight per-day tally of Gemini requests, tokens, and errors -
    so you can see a quota problem coming in `python -m src.status`.
    Best-effort; never raises.
    """

    def __init__(self, path: Path = _FILE) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            self._data = {}

    def _day(self) -> dict:
        d = self._today_key = _today()
        self._data.setdefault(
            d,
            {"requests": 0, "input_tokens": 0, "output_tokens": 0,
             "errors": {}},
        )
        return self._data[d]

    def _save(self) -> None:
        try:
            # keep only the last 14 days
            keys = sorted(self._data)[-14:]
            self._data = {k: self._data[k] for k in keys}
            self._path.write_text(
                json.dumps(self._data, indent=2), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass

    # ----------------------------------------------------------------

    def record(
        self, input_tokens: int = 0, output_tokens: int = 0
    ) -> None:
        with self._lock:
            day = self._day()
            day["requests"] += 1
            day["input_tokens"] += max(0, int(input_tokens or 0))
            day["output_tokens"] += max(0, int(output_tokens or 0))
            self._save()

    def record_error(self, kind: str) -> None:
        with self._lock:
            day = self._day()
            day["errors"][kind] = day["errors"].get(kind, 0) + 1
            self._save()

    def today(self) -> dict:
        with self._lock:
            return dict(self._day())


# Process-wide singleton so any component can report without wiring.
USAGE = UsageTracker()


def record_response(response: object) -> None:
    """Pull usage_metadata off a genai response/message and tally it."""

    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        USAGE.record()
        return

    USAGE.record(
        getattr(meta, "prompt_token_count", 0)
        or getattr(meta, "input_token_count", 0)
        or 0,
        getattr(meta, "candidates_token_count", 0)
        or getattr(meta, "output_token_count", 0)
        or getattr(meta, "response_token_count", 0)
        or 0,
    )
