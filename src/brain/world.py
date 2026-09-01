"""What is going on in your life, as opposed to on your machine.

Alfred remembered a hundred and twenty-seven things. Sixty-four were
about the computer, sixty-two were lessons about its own tools, and one
was about the person it works for. So when it spoke unprompted it talked
about disk space, because disk space was all it knew.

The material for something better was already there and unassembled:
coursework with deadlines, a calendar, an inbox with people in it, and a
record of what you actually spend your hours on. This is the thing that
puts them together - people, the things you are meant to be doing, and
when they are due - so the brain has something about YOU to be proactive
about.

It is deliberately not another memory. Memory holds what was said; this
holds what is true right now and stops being true later. A deadline that
has passed is not a fact to keep, it is a thing to drop.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS matters (
    id        TEXT PRIMARY KEY,
    kind      TEXT NOT NULL,      -- person | doing | due
    name      TEXT NOT NULL,
    detail    TEXT NOT NULL DEFAULT '',
    due       TEXT,               -- ISO, for things with a deadline
    state     TEXT NOT NULL DEFAULT 'open',   -- open | done | dropped
    source    TEXT NOT NULL DEFAULT '',       -- classroom | calendar | mail | you
    weight    INTEGER NOT NULL DEFAULT 1,     -- how often it has come up
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS matters_due ON matters(state, due);
CREATE INDEX IF NOT EXISTS matters_kind ON matters(kind, state);
"""


@dataclass
class Matter:
    kind: str
    name: str
    detail: str = ""
    due: datetime | None = None
    source: str = ""

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.source}:{self.name}".lower()[:180]


class World:
    """People, things you are doing, and things that are due."""

    def __init__(self, path: Path | str) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------ writing

    def note(self, matter: Matter, now: datetime | None = None) -> None:
        """Something is true. Said again, it matters more."""
        now = (now or datetime.now()).isoformat(timespec="seconds")
        due = matter.due.isoformat(timespec="minutes") if matter.due else None

        with self._lock:
            self._conn.execute(
                "INSERT INTO matters (id, kind, name, detail, due, source, "
                "first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  detail = excluded.detail,"
                "  due = COALESCE(excluded.due, matters.due),"
                "  last_seen = excluded.last_seen,"
                "  weight = matters.weight + 1,"
                # Something seen again is current again. A deadline that
                # comes back after being marked done has been reopened.
                "  state = CASE WHEN matters.state = 'dropped' THEN 'open' "
                "               ELSE matters.state END",
                (matter.id, matter.kind, matter.name, matter.detail, due,
                 matter.source, now, now),
            )
            self._conn.commit()

    def settle(self, matter_id: str, state: str = "done") -> bool:
        with self._lock:
            changed = self._conn.execute(
                "UPDATE matters SET state = ? WHERE id = ?", (state, matter_id)
            ).rowcount
            self._conn.commit()
        return bool(changed)

    def forget_stale(self, days: int = 30, now: datetime | None = None) -> int:
        """Drop what has gone quiet.

        This is a picture of now, not an archive. A thing nobody has
        mentioned in a month and that has no deadline left is not part
        of your life any more, and keeping it makes the picture worse.
        """
        now = now or datetime.now()
        cutoff = (now - timedelta(days=days)).isoformat(timespec="seconds")
        with self._lock:
            gone = self._conn.execute(
                "DELETE FROM matters WHERE last_seen < ? AND "
                "(due IS NULL OR due < ?)",
                (cutoff, now.isoformat(timespec="minutes")),
            ).rowcount
            self._conn.commit()
        return gone

    # ------------------------------------------------------------ reading

    def due_soon(self, days: int = 7, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now()
        horizon = (now + timedelta(days=days)).isoformat(timespec="minutes")
        return self._rows(
            "SELECT * FROM matters WHERE state = 'open' AND due IS NOT NULL "
            "AND due <= ? ORDER BY due",
            (horizon,),
        )

    def overdue(self, now: datetime | None = None) -> list[dict]:
        now = (now or datetime.now()).isoformat(timespec="minutes")
        return self._rows(
            "SELECT * FROM matters WHERE state = 'open' AND due IS NOT NULL "
            "AND due < ? ORDER BY due",
            (now,),
        )

    def of_kind(self, kind: str, limit: int = 12) -> list[dict]:
        return self._rows(
            "SELECT * FROM matters WHERE kind = ? AND state = 'open' "
            "ORDER BY weight DESC, last_seen DESC LIMIT ?",
            (kind, limit),
        )

    def all_open(self) -> list[dict]:
        return self._rows(
            "SELECT * FROM matters WHERE state = 'open' "
            "ORDER BY weight DESC, last_seen DESC", ()
        )

    def _rows(self, sql: str, args: tuple) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------- saying

    def brief(self, now: datetime | None = None, limit: int = 5) -> str:
        """The short version, for putting in front of a model.

        Ordered by what would worry a person: what is late, what is
        close, who is waiting, what you are in the middle of.
        """
        now = now or datetime.now()
        lines: list[str] = []

        late = self.overdue(now)
        if late:
            lines.append("Overdue: " + "; ".join(
                f"{m['name']} ({_when(m['due'], now)})" for m in late[:limit]
            ))

        soon = [m for m in self.due_soon(7, now) if m not in late]
        if soon:
            lines.append("Due soon: " + "; ".join(
                f"{m['name']} ({_when(m['due'], now)})" for m in soon[:limit]
            ))

        waiting = self.of_kind("person", limit)
        if waiting:
            lines.append("People: " + "; ".join(
                f"{m['name']}" + (f" - {m['detail'][:50]}" if m["detail"] else "")
                for m in waiting
            ))

        doing = self.of_kind("doing", limit)
        if doing:
            lines.append("You are working on: " + "; ".join(
                m["name"] for m in doing
            ))

        return "\n".join(lines)


def _when(due: str | None, now: datetime) -> str:
    if not due:
        return ""
    try:
        at = datetime.fromisoformat(due)
    except Exception:  # noqa: BLE001
        return due

    days = (at.date() - now.date()).days
    if days < -1:
        return f"{-days} days ago"
    if days == -1:
        return "yesterday"
    if days == 0:
        return f"today {at:%H:%M}"
    if days == 1:
        return f"tomorrow {at:%H:%M}"
    if days < 7:
        return f"{at:%A}"
    return f"{at:%d %b}"


# ------------------------------------------------------------ gathering


def refresh(
    world: World,
    classroom: Any = None,
    calendar: Any = None,
    mail: Any = None,
    activity: Any = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Read the sources Alfred already has and write down what matters.

    Every source is optional and every one is guarded: a picture of your
    life should not fail to exist because one mailbox is having a bad
    morning.
    """
    now = now or datetime.now()
    counted = {"due": 0, "person": 0, "doing": 0}

    for gather in (
        lambda: _from_classroom(world, classroom, now),
        lambda: _from_calendar(world, calendar, now),
        lambda: _from_mail(world, mail, now),
        lambda: _from_activity(world, activity, now),
    ):
        try:
            for kind, n in gather().items():
                counted[kind] = counted.get(kind, 0) + n
        except Exception as exc:  # noqa: BLE001
            print(f"[World] a source could not be read: {exc}")

    return counted


def _from_classroom(world: World, classroom: Any, now: datetime) -> dict[str, int]:
    if classroom is None:
        return {}
    n = 0
    for work in classroom.due(days=21):
        if work.get("handed_in"):
            continue
        try:
            when = datetime.fromisoformat(work["due_iso"])
        except Exception:  # noqa: BLE001
            when = None
        world.note(Matter(
            kind="due", name=work["title"], detail=work.get("course", ""),
            due=when, source="classroom",
        ), now)
        n += 1
    return {"due": n}


def _from_calendar(world: World, calendar: Any, now: datetime) -> dict[str, int]:
    if calendar is None:
        return {}
    n = 0
    for event in calendar.agenda(days=14, now=now):
        # All-day events used to be skipped outright, which quietly
        # threw away birthdays and deadlines - exactly the things worth
        # mentioning the day before.
        world.note(Matter(
            kind="due", name=event["title"], detail=event.get("where", ""),
            due=_parse(event.get("starts_iso") or event.get("starts", "")),
            source="calendar",
        ), now)
        n += 1
        for who in event.get("with", []):
            if who:
                world.note(Matter(
                    kind="person", name=_name_of(who),
                    detail=f"in your diary: {event['title'][:40]}",
                    source="calendar",
                ), now)
    return {"due": n}


def _from_mail(world: World, mail: Any, now: datetime) -> dict[str, int]:
    if mail is None:
        return {}
    n = 0
    for message in mail.unread(limit=15):
        # A shop is not somebody who is waiting on you.
        sender = message.get("from", "")
        if message.get("bulk") or not _is_a_person(sender):
            continue
        who = _name_of(sender)
        if not who:
            continue
        world.note(Matter(
            kind="person", name=who,
            detail=f"unread: {message.get('subject', '')[:50]}",
            source="mail",
        ), now)
        n += 1
    return {"person": n}


def _from_activity(world: World, activity: Any, now: datetime) -> dict[str, int]:
    if activity is None:
        return {}
    n = 0
    # An hour of your day is a thing you are doing. Ten minutes is not.
    for spell in activity.today(now):
        if spell["seconds"] < 3600:
            continue
        world.note(Matter(
            kind="doing", name=spell["app"],
            detail=f"{spell['seconds'] // 3600}h today", source="activity",
        ), now)
        n += 1
    return {"doing": n}


def _parse(text: str) -> datetime | None:
    """A date out of whatever the source called a date."""
    text = (text or "").strip()
    if not text:
        return None

    # Google's own format, which is what should be arriving now: a full
    # timestamp, or a bare "2026-09-08" for something lasting all day.
    try:
        at = datetime.fromisoformat(text)
        return at.replace(tzinfo=None) if at.tzinfo else at
    except Exception:  # noqa: BLE001
        pass

    for shape in ("%a %d %b %H:%M", "%d %b %H:%M"):
        try:
            at = datetime.strptime(text, shape)
            return at.replace(year=date.today().year)
        except Exception:  # noqa: BLE001
            continue
    try:
        return datetime.fromisoformat(text)
    except Exception:  # noqa: BLE001
        return None



# Addresses nobody is sitting behind.
_NOT_A_PERSON = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "notifications",
    "notification", "info", "support", "hello", "team", "news", "updates",
    "alerts", "alert", "billing", "accounts", "account", "contact", "admin",
    "service", "services", "help", "care", "marketing", "mailer", "post",
    "newsletter", "orders", "receipts", "no_reply",
}


def _is_a_person(sender: str) -> bool:
    """Would you say this name out loud as somebody who wants you?

    Gmail's categories and List-Unsubscribe catch most of it, but the
    senders that set neither were still arriving as people: an NHS
    prescription service and a student discount card were, briefly, two
    of the closest relationships Alfred believed you had.

    The rule is deliberately shy. It rejects only on loud evidence -
    a role address, digits in the name, shouting - because a person
    wrongly dropped here never comes back on their own, while a shop
    wrongly kept is merely noise.
    """
    sender = (sender or "").strip()

    address = sender.split("<", 1)[1].rstrip(">") if "<" in sender else sender
    local = address.split("@", 1)[0].strip().lower()
    if local in _NOT_A_PERSON:
        return False

    name = sender.split("<", 1)[0].strip().strip('"') if "<" in sender else ""
    if not name:
        return True

    # A date, an order number, a reference: not something you are called.
    if any(ch.isdigit() for ch in name):
        return False

    letters = [ch for ch in name if ch.isalpha()]
    if len(letters) > 6 and sum(ch.isupper() for ch in letters) > len(letters) * 0.6:
        return False

    return True

def _name_of(address: str) -> str:
    """"Sam Green <sam@x.com>" -> "Sam Green". A person, not an inbox."""
    address = (address or "").strip()
    if "<" in address:
        address = address.split("<", 1)[0].strip().strip('"')
    if not address:
        return ""
    if "@" in address and " " not in address:
        address = address.split("@", 1)[0].replace(".", " ").title()
    return address[:60]
