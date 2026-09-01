"""
python -m src.costs  -  what Alfred costs to run, per month.

Two questions, kept apart because they have different answers:

    observed    what the days already recorded would have cost
    projected   what a month of ordinary use would cost

The recorded days are testing days, not living-with-it days, so the
first is not a forecast. It is the honest floor: this much traffic
really happened and would really have been billed.

Every constant below is measured on this machine rather than guessed,
and says where it came from. Two of them were guessed once and both
were wrong in the flattering direction, which is the reason for the
rule: a task is not one model call, it is about seven, and a brain
that thinks every ninety seconds costs money on a day you never speak
to it at all.

Voice is priced per minute of speech rather than per token, because
that is the only unit anybody can estimate about themselves. Nobody
knows how many tokens they say. Everybody knows roughly how many
minutes a day they would talk to it.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# Paid-tier list prices, US dollars per million tokens, from
# ai.google.dev/gemini-api/docs/pricing. Written down with the date they
# were read, so a stale number is visible as stale rather than quietly
# wrong.
PRICES_READ_ON = "2026-09-01"

TEXT_RATES: dict[str, tuple[float, float]] = {
    # model                        in     out
    "gemini-flash-lite-latest": (0.30, 2.50),
    "gemini-flash-latest": (0.30, 2.50),
    "gemini-embedding-001": (0.15, 0.00),
}

# Live API. Audio is the expensive half and output the expensive
# direction: Alfred talking costs 3.6x Alfred listening.
LIVE_AUDIO_IN_PER_MIN = 0.005
LIVE_AUDIO_OUT_PER_MIN = 0.018
LIVE_TEXT_IN = 0.75      # per million, for context re-read each turn

# ---------------------------------------------------------------- measured

# alfred_usage.json, 2026-08-31: 470,306 in / 11,021 out over 194 calls.
TOKENS_IN_PER_CALL = 2400
TOKENS_OUT_PER_CALL = 57

# alfred_brain_audit.sqlite3, counted by kind over 10 real tasks:
#   plan 0.9, step 3.0, verify 1.6, reflect 1.2
# A task is seven model calls, not one. Costing it as one under-read
# the bill by that whole factor.
CALLS_PER_TASK = 6.7

# ALFRED_BRAIN_TICK_SECONDS=90, and on the busiest recorded day 95 of
# 660 ticks escalated to a model call. This is what Alfred costs while
# you are asleep, and it is the line that surprises people.
TICK_SECONDS = 90.0
DELIBERATE_SHARE = 95 / 660

TOKENS_PER_PHOTO = 1300
LIVE_CONTEXT_PER_SPOKEN_MIN = 3000


@dataclass
class Shape:
    """A guess about a day, stated out loud so it can be argued with."""

    name: str
    tasks: int                # things asked of it that need planning
    speaking_minutes: float   # how long Alfred talks
    listening_minutes: float  # how long the mic is actually open
    photos: int               # images and screenshots it reads
    awake_hours: float        # how long the brain is running at all


SHAPES = [
    Shape("Light", 15, 3, 8, 1, awake_hours=10),
    Shape("Typical", 40, 10, 25, 4, awake_hours=16),
    Shape("Heavy", 120, 30, 75, 15, awake_hours=24),
]


def text_cost(tokens_in: float, tokens_out: float, model: str) -> float:
    rate_in, rate_out = TEXT_RATES.get(
        model, TEXT_RATES["gemini-flash-lite-latest"]
    )
    return tokens_in / 1e6 * rate_in + tokens_out / 1e6 * rate_out


def calls_cost(calls: float) -> float:
    return text_cost(
        calls * TOKENS_IN_PER_CALL,
        calls * TOKENS_OUT_PER_CALL,
        "gemini-flash-lite-latest",
    )


def day_cost(shape: Shape) -> dict[str, float]:
    """What one day of that shape costs, split by where it goes."""
    ticks = shape.awake_hours * 3600 / TICK_SECONDS
    idling = calls_cost(ticks * DELIBERATE_SHARE)
    thinking = calls_cost(shape.tasks * CALLS_PER_TASK)
    looking = text_cost(
        shape.photos * TOKENS_PER_PHOTO, shape.photos * 80,
        "gemini-flash-lite-latest",
    )

    talking = shape.speaking_minutes * LIVE_AUDIO_OUT_PER_MIN
    hearing = shape.listening_minutes * LIVE_AUDIO_IN_PER_MIN
    # Each turn re-reads the conversation so far. The sliding window
    # caps how far back that reaches, which is the whole reason this is
    # a small number rather than a runaway one.
    context = (
        shape.speaking_minutes * LIVE_CONTEXT_PER_SPOKEN_MIN
        / 1e6 * LIVE_TEXT_IN
    )

    return {
        "idling": idling,
        "thinking": thinking,
        "looking": looking,
        "talking": talking,
        "hearing": hearing,
        "context": context,
    }


def observed(path: Path) -> list[tuple[str, int, float]]:
    """What the recorded days would really have been billed."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []

    out = []
    for day in sorted(data):
        entry = data[day]
        requests = int(entry.get("requests") or 0)
        if not requests:
            continue
        out.append((
            day,
            requests,
            text_cost(
                int(entry.get("input_tokens") or 0),
                int(entry.get("output_tokens") or 0),
                "gemini-flash-lite-latest",
            ),
        ))
    return out


def failovers(path: Path) -> int:
    """Times Gemini was too busy and the work fell to a lesser model."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    return sum(
        n for day in data.values()
        for key, n in (day.get("errors") or {}).items()
        if key.startswith("plan_failover:")
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    usage = _ROOT / "alfred_usage.json"

    print(f"Gemini paid-tier list prices, read {PRICES_READ_ON}.\n")

    days = observed(usage)
    if days:
        print("Already happened - what these days would have cost:\n")
        for day, requests, cost in days:
            print(f"  {day}   {requests:>4} calls    ${cost:,.3f}")
        worst = max(c for _, _, c in days)
        print(f"\n  Busiest recorded day: ${worst:,.3f}"
              f"  ->  ${worst * 30:,.2f} if every day looked like it.")
        print("  Those were testing days, heavier than living with it.\n")

    print("A month of ordinary use:\n")
    print(f"  {'':9} {'idle':>6} {'think':>7} {'look':>6} {'talk':>6}"
          f" {'hear':>6} {'ctx':>6}    {'MONTH':>7}")

    for shape in SHAPES:
        parts = day_cost(shape)
        month = sum(parts.values()) * 30
        print(
            f"  {shape.name:9} "
            f"{parts['idling'] * 30:>6.2f} {parts['thinking'] * 30:>7.2f} "
            f"{parts['looking'] * 30:>6.2f} {parts['talking'] * 30:>6.2f} "
            f"{parts['hearing'] * 30:>6.2f} {parts['context'] * 30:>6.2f} "
            f"   ${month:>6.2f}"
        )

    print(
        "\n  idle  = the brain thinking on its own, every 90s, unprompted"
        "\n  think = planning and carrying out what you ask"
        "\n  look  = photos and screenshots it reads"
        "\n  talk  = Alfred speaking      hear = the mic actually open"
        "\n  ctx   = re-reading the conversation each turn"
    )

    fell = failovers(usage)
    if fell:
        print(
            f"\nWhat a paid key buys: {fell:,} times so far the free quota"
            "\nran out mid-job and the work fell to the local 4B, which is"
            "\nslower and less accurate. That stops."
        )

    print(
        "\nWhat keeps the bill small: the wake word means the mic is not"
        "\nstreaming all day, the planner is Flash-Lite rather than a big"
        "\nmodel, and the local 4B still absorbs work for free."
        "\n\nTo spend less, lengthen ALFRED_BRAIN_TICK_SECONDS. The idle"
        "\ncolumn is the one you pay whether or not you use it."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
