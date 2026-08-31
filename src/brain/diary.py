"""What Alfred actually did today.

Everything was already written down and none of it was readable. Tasks
in one file, what it said aloud in another, what it noticed in a third,
what it learned in a fourth, what it could not get past in a fifth. A
person who wanted to know how the day had gone could read six SQLite
tables or ask Alfred, and asking Alfred got them whatever it happened to
remember about the last few minutes.

So the facts are gathered from the files that hold them, and the model's
only job is to put them in sentences. It is not asked what happened - it
is told, and asked to say it plainly. That matters: an account
assembled from memory is a story, and this has to be a record.

The other rule is that it says what went wrong. An assistant that only
reports its successes is not giving an account of itself, it is
managing you.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass
class Day:
    """What is on the record for one day."""

    on: date
    asked: list[dict] = field(default_factory=list)      # jobs the user set
    own: list[dict] = field(default_factory=list)        # jobs it set itself
    said: list[str] = field(default_factory=list)        # spoken unprompted
    learned: list[str] = field(default_factory=list)     # new routines
    stuck: list[dict] = field(default_factory=list)      # walls hit
    noticed: list[str] = field(default_factory=list)     # about the machine
    where: list[dict] = field(default_factory=list)      # where time went

    @property
    def quiet(self) -> bool:
        return not (self.asked or self.own or self.said or self.learned)

    def facts(self) -> str:
        """The record, as plainly as it can be put without a model."""
        out: list[str] = []

        def section(title: str, lines: list[str]) -> None:
            if lines:
                out.append(title)
                out.extend("  " + line for line in lines)

        section("JOBS YOU ASKED FOR:", [
            f"{t['goal']} - {t['status']}"
            + (f" ({t['summary'][:120]})" if t["summary"] else "")
            for t in self.asked
        ])
        section("JOBS IT STARTED ITSELF:", [
            f"{t['goal']} - {t['status']}" for t in self.own
        ])
        section("THINGS IT SAID UNPROMPTED:", self.said[:8])
        section("ROUTINES IT LEARNED:", self.learned)
        section("THINGS IT COULD NOT GET PAST:", [
            f"{w['tool']}: {w['detail'][:90]} ({w['hits']} times"
            + (", found a way round" if w["workaround"] else ", still stuck")
            + ")"
            for w in self.stuck
        ])
        section("THINGS IT NOTICED ABOUT THE MACHINE:", self.noticed[:6])
        section("WHERE YOUR TIME WENT:", [
            f"{w['app']} {w['seconds'] // 3600}h {(w['seconds'] % 3600) // 60:02d}m"
            for w in self.where[:6]
        ])

        return "\n".join(out) or "Nothing on the record."


# ------------------------------------------------------------ gathering


def gather(root: Path | str, on: date | None = None) -> Day:
    root = Path(root)
    on = on or date.today()
    day = Day(on=on)

    start = datetime.combine(on, datetime.min.time()).isoformat()
    end = datetime.combine(on + timedelta(days=1), datetime.min.time()).isoformat()

    for task in _rows(
        root / "alfred_tasks.sqlite3",
        "SELECT goal, status, summary, source FROM tasks "
        "WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
        (start, end),
    ):
        entry = {
            "goal": task["goal"],
            "status": task["status"],
            "summary": task["summary"] or "",
        }
        # "voice" means a person asked for it, whichever way they asked.
        (day.asked if task["source"] == "voice" else day.own).append(entry)

    for row in _rows(
        root / "alfred_brain_audit.sqlite3",
        "SELECT kind, payload FROM brain_audit "
        "WHERE created_at >= ? AND created_at < ? AND kind IN "
        "('spoken', 'notable', 'scheduled_reminder', 'scheduled_task') "
        "ORDER BY id",
        (start, end),
    ):
        said = _payload(row["payload"])
        if row["kind"] == "spoken":
            text = str(said.get("text") or said.get("summary") or "").strip()
            if text:
                day.said.append(text[:200])
        elif row["kind"] == "notable":
            summary = str(said.get("summary") or "").strip()
            if summary and summary not in day.noticed:
                day.noticed.append(summary[:160])
        else:
            goal = str(said.get("goal") or "").strip()
            if goal:
                day.own.append({
                    "goal": goal, "status": "scheduled", "summary": "",
                })

    for skill in _rows(
        root / "alfred_skills.sqlite3",
        "SELECT template FROM skills WHERE created_at >= ? AND created_at < ?",
        (start, end),
    ):
        day.learned.append(skill["template"])

    for wall in _rows(
        root / "alfred_limitations.sqlite3",
        "SELECT tool, detail, hits, workaround FROM limitations "
        "WHERE last_seen >= ? AND last_seen < ? ORDER BY hits DESC",
        (start, end),
    ):
        day.stuck.append(dict(wall))

    for spell in _rows(
        root / "alfred_activity.sqlite3",
        "SELECT app, SUM(seconds) AS seconds FROM spells "
        "WHERE started >= ? AND started < ? GROUP BY app "
        "ORDER BY seconds DESC",
        (start, end),
    ):
        day.where.append(dict(spell))

    return day


def _rows(path: Path, sql: str, args: tuple) -> list[Any]:
    """Whatever that file can tell us, or nothing.

    A day's account should not fail because one store is missing, being
    written to, or older than the column being asked for.
    """
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return []


def _payload(raw: Any) -> dict:
    try:
        loaded = json.loads(raw or "{}")
        return loaded if isinstance(loaded, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# ------------------------------------------------------------- telling


_SYSTEM = """You are Alfred, telling the person whose computer you run on \
what you did today. You are given the record. Put it in plain sentences.

- Talk about yourself in the first person, to them in the second.
- SHORT sentences. One idea each. Never a list of six things joined by
  commas - that is unreadable, and it is the most likely thing to go
  wrong here.
- Follow the record's order: what they asked for, then anything you did
  on your own, then anything you learned, then anything you could not
  get past.
- Group the repetitive. "Checked whether Steam was running, three times"
  is better than saying it three times.
- Say what went wrong plainly and never soften it. If something did not
  work, say so and say what stopped it. A failure left out is the one
  thing that makes the whole account worthless.
- Do not pad. A quiet day is two sentences. Do not invent significance.
- Do not praise yourself, do not thank them, do not offer help at the end.
- Everything must come from the record below. If it is not there, you
  did not do it.
- Eight short sentences at the very most.

Shape it like this:

    You asked me for six things. Four worked.
    The screenshot ran out of time - the snip tool needs a person.
    On my own I ran the morning inbox summary at eight.
    I learned two new routines, including opening MultiMC.
    I got stuck twice on window titles I could not match.
"""


def tell(day: Day, chat: Any = None) -> str:
    """The day, in sentences. Falls back to the plain record."""
    if day.quiet and not day.stuck:
        return f"Nothing much on {day.on:%A}. No jobs, nothing learned."

    if chat is None:
        return day.facts()

    prompt = (
        f"THE RECORD FOR {day.on:%A %d %B}:\n\n{day.facts()}\n\n"
        "Tell them how the day went:"
    )
    try:
        said = chat.generate(prompt, system=_SYSTEM, temperature=0.3,
                             max_tokens=400).strip()
    except Exception:  # noqa: BLE001
        return day.facts()

    return said or day.facts()
