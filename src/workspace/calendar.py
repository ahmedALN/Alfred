"""What is on, and putting something on it.

Read and add. Not remove, not overwrite - and unlike mail, that limit is
kept here rather than by Google, because there is no permission that
grants "add events" without also granting "delete events". A weaker
promise, stated as one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

_MAX = 50


class Calendar:
    def __init__(self, account: Any) -> None:
        self._account = account

    def _api(self):
        return self._account.service("calendar", "v3")

    # ------------------------------------------------------------ reading

    def agenda(
        self, days: int = 1, now: datetime | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """What is on between now and ``days`` from now."""
        now = _aware(now or datetime.now())
        until = now + timedelta(days=max(1, days))

        found = self._api().events().list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=until.isoformat(),
            singleEvents=True,          # expand repeats into occurrences
            orderBy="startTime",
            maxResults=max(1, min(limit, _MAX)),
        ).execute()

        return [_tidy(e) for e in (found.get("items") or [])]

    def next_up(self, now: datetime | None = None) -> dict[str, Any] | None:
        upcoming = self.agenda(days=14, now=now, limit=1)
        return upcoming[0] if upcoming else None

    def free_between(
        self, start: datetime, end: datetime
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Is that window clear, and if not, what is in the way?"""
        busy = self._api().events().list(
            calendarId="primary",
            timeMin=_aware(start).isoformat(),
            timeMax=_aware(end).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=_MAX,
        ).execute()

        clashes = [
            _tidy(e) for e in (busy.get("items") or [])
            # An all-day event is not a reason to say somebody is busy
            # between two and three in the afternoon.
            if "dateTime" in (e.get("start") or {})
        ]
        return (not clashes), clashes

    # ------------------------------------------------------------ writing

    def add(
        self,
        title: str,
        start: datetime,
        minutes: int = 60,
        where: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "summary": title,
            "start": {"dateTime": start.astimezone().isoformat()},
            "end": {
                "dateTime": (
                    start + timedelta(minutes=max(1, minutes))
                ).astimezone().isoformat()
            },
        }
        if where:
            body["location"] = where
        if notes:
            body["description"] = notes

        made = self._api().events().insert(
            calendarId="primary", body=body
        ).execute()

        return {
            "id": made.get("id", ""),
            "title": title,
            "starts": _readable(made.get("start")),
            "link": made.get("htmlLink", ""),
        }


# ------------------------------------------------------------- helpers



def _aware(moment: datetime) -> datetime:
    """A time Google will accept.

    The Calendar API wants RFC3339 with an offset and answers 400 to
    anything without one. Every caller here reaches for datetime.now(),
    which is naive, so the read side had never once worked: agenda
    returned a Bad Request, next_up sits on top of agenda, and the
    picture of your life therefore had no appointments in it and no
    error to explain why.

    A naive time means local time. Say so, rather than sending it.
    """
    return moment if moment.tzinfo else moment.astimezone()


def _tidy(event: dict[str, Any]) -> dict[str, Any]:
    start = event.get("start") or {}
    return {
        "id": event.get("id", ""),
        "title": event.get("summary", "(untitled)"),
        "starts": _readable(start),
        # The pretty one is for saying out loud. This one is for
        # working with: "Tue 08 Sep (all day)" cannot be parsed back
        # into a date, and everything downstream was trying to.
        "starts_iso": start.get("dateTime") or start.get("date") or "",
        "all_day": "date" in start and "dateTime" not in start,
        "where": event.get("location", ""),
        "with": [
            a.get("email", "")
            for a in (event.get("attendees") or [])
            if not a.get("self")
        ][:6],
    }


def _readable(when: dict[str, Any] | None) -> str:
    when = when or {}
    raw = when.get("dateTime") or when.get("date") or ""
    if not raw:
        return ""
    try:
        if "T" not in raw:
            return datetime.fromisoformat(raw).strftime("%a %d %b (all day)")
        moment = datetime.fromisoformat(raw)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone().strftime("%a %d %b %H:%M")
    except Exception:  # noqa: BLE001
        return raw
