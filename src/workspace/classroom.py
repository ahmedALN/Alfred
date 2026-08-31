"""Courses, and what is due.

Read-only throughout, by permission and not by restraint: Alfred asks
Google for the readonly scopes and holds nothing that could submit work,
join a class, or change a grade. Being told what is due is the useful
half; the other half should be a person's own doing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

_MAX = 50


class Classroom:
    def __init__(self, account: Any) -> None:
        self._account = account

    def _api(self):
        return self._account.service("classroom", "v1")

    # ------------------------------------------------------------ courses

    def courses(self, active_only: bool = True) -> list[dict[str, Any]]:
        found = self._api().courses().list(
            courseStates=["ACTIVE"] if active_only else None,
            pageSize=_MAX,
        ).execute()

        return [
            {
                "id": c.get("id", ""),
                "name": c.get("name", ""),
                "section": c.get("section", ""),
                "teacher": c.get("ownerId", ""),
            }
            for c in (found.get("courses") or [])
        ]

    # ------------------------------------------------------------- work

    def due(
        self, days: int = 14, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Everything with a deadline inside the next ``days``.

        Sorted by when it is due rather than by course, because that is
        the order it has to be done in.
        """
        now = now or datetime.now()
        horizon = now + timedelta(days=max(1, days))
        out: list[dict[str, Any]] = []

        for course in self.courses():
            for piece in self._work(course["id"]):
                when = _due_at(piece)
                if when is None or when > horizon:
                    continue
                out.append({
                    "course": course["name"],
                    "title": piece.get("title", "(untitled)"),
                    "due": when.strftime("%a %d %b %H:%M"),
                    "due_iso": when.isoformat(timespec="minutes"),
                    "overdue": when < now,
                    "handed_in": self._handed_in(course["id"], piece.get("id")),
                    "link": piece.get("alternateLink", ""),
                })

        return sorted(out, key=lambda w: w["due_iso"])

    def _work(self, course_id: str) -> list[dict[str, Any]]:
        try:
            found = self._api().courses().courseWork().list(
                courseId=course_id, pageSize=_MAX,
            ).execute()
            return found.get("courseWork") or []
        except Exception:  # noqa: BLE001
            # A course that refuses its coursework should not take the
            # rest of the timetable down with it.
            return []

    def _handed_in(self, course_id: str, work_id: str | None) -> bool:
        if not work_id:
            return False
        try:
            found = self._api().courses().courseWork().studentSubmissions()\
                .list(
                    courseId=course_id, courseWorkId=work_id, userId="me",
                ).execute()
            states = [
                s.get("state", "")
                for s in (found.get("studentSubmissions") or [])
            ]
            return any(s in ("TURNED_IN", "RETURNED") for s in states)
        except Exception:  # noqa: BLE001
            return False

    # ---------------------------------------------------------- notices

    def announcements(self, limit: int = 10) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for course in self.courses():
            try:
                found = self._api().courses().announcements().list(
                    courseId=course["id"], pageSize=limit,
                ).execute()
            except Exception:  # noqa: BLE001
                continue
            for note in (found.get("announcements") or []):
                out.append({
                    "course": course["name"],
                    "text": (note.get("text") or "")[:400],
                    "when": (note.get("updateTime") or "")[:16].replace("T", " "),
                })
        return sorted(out, key=lambda a: a["when"], reverse=True)[:limit]


def _due_at(piece: dict[str, Any]) -> datetime | None:
    """Classroom splits a deadline across two fields, and the time half
    is optional. No date at all means no deadline."""
    date = piece.get("dueDate") or {}
    if not date.get("year"):
        return None

    at = piece.get("dueTime") or {}
    try:
        return datetime(
            int(date["year"]), int(date["month"]), int(date["day"]),
            int(at.get("hours", 23)), int(at.get("minutes", 59)),
        )
    except Exception:  # noqa: BLE001
        return None
