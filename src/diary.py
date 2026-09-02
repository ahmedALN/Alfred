"""
python -m src.diary  -  what Alfred did.

    (nothing)     today
    yesterday     yesterday
    2026-08-31    that day
    --raw         the record itself, unphrased

Everything was already written down and none of it was readable: tasks
in one file, what it said in another, what it learned in a third, what
it could not get past in a fourth. This reads them and says how the day
went - including the parts that did not work, which is the only reason
an account is worth having.
"""

from __future__ import annotations

import logging
import sys
from datetime import date, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# The SDK warns about a call path we use deliberately, on every call.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)


def _day_from(argv: list[str]) -> date:
    for arg in argv:
        if arg.startswith("-"):
            continue
        if arg == "today":
            return date.today()
        if arg == "yesterday":
            return date.today() - timedelta(days=1)
        try:
            return date.fromisoformat(arg)
        except ValueError:
            print(f"I don't know what day {arg!r} is. Try 2026-08-31.")
            raise SystemExit(1)  # noqa: B904
    return date.today()


def _voice():
    """The fast model - this is phrasing, not judgement."""
    try:
        from google import genai

        from src.ai.providers.factory import build_providers
        from src.config import load_settings

        settings = load_settings()
        return build_providers(
            settings, genai.Client(api_key=settings.gemini_api_key)
        ).fast_chat
    except Exception:  # noqa: BLE001
        return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0

    from src.brain.diary import gather, tell

    day = gather(_ROOT, _day_from(argv))

    if "--raw" in argv:
        print(day.facts())
        return 0

    print(tell(day, _voice()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
