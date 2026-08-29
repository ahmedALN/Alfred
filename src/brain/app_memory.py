from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS apps (
    key          TEXT PRIMARY KEY,
    display      TEXT NOT NULL,
    window_title TEXT NOT NULL DEFAULT '',
    opens        INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    last_used    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS app_controls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    app_key      TEXT NOT NULL,
    name         TEXT NOT NULL,
    action       TEXT NOT NULL,
    control_type TEXT NOT NULL DEFAULT '',
    automation_id TEXT NOT NULL DEFAULT '',
    uses         INTEGER NOT NULL DEFAULT 1,
    last_used    TEXT NOT NULL,
    UNIQUE(app_key, name, action)
);

CREATE TABLE IF NOT EXISTS app_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    app_key    TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'note',
    note       TEXT NOT NULL,
    uses       INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(app_key, note)
);

CREATE INDEX IF NOT EXISTS idx_app_controls_key ON app_controls(app_key);
CREATE INDEX IF NOT EXISTS idx_app_notes_key ON app_notes(app_key);
"""

# Words that appear in window titles but aren't part of the app's identity.
_TITLE_NOISE = re.compile(
    r"\b(premium|free|pro|beta|preview|home|personal|trial|"
    r"microsoft|windows|app)\b", re.I,
)
_PUNCT = re.compile(r"[^\w\s]+")


def app_key(name: str) -> str:
    """Normalise an app name or window title into a stable key."""
    text = (name or "").strip().lower()
    if not text:
        return ""
    # "Spotify Premium - Song Name" / "Document - Notepad"
    for sep in (" - ", " – ", " — ", " | "):
        if sep in text:
            parts = [p.strip() for p in text.split(sep) if p.strip()]
            if parts:
                # The app is usually the shortest part (a title is longer).
                text = min(parts, key=len)
            break
    text = text.removesuffix(".exe")
    text = _TITLE_NOISE.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    return " ".join(text.split())[:40]


class AppMemory:
    """
    What Alfred has learned about working *inside* specific apps.

    Distinct from the skill library (which replays a whole task verbatim
    for one phrasing): this is per-app knowledge that pays off even for a
    brand-new request in a familiar app - the real window title, the
    control names that actually worked, and any quirks. Feeding this into
    the planner and executor removes most of the exploratory 'tree'
    calls, which is where multi-step in-app work spends its time.

    Thread-safe.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------ writes

    def _ensure_app(self, key: str, display: str, opens: int = 0,
                    window_title: str = "") -> None:
        """Create the parent row if needed. Called by every writer so a
        control or note learned without an explicit open still shows up."""
        now = _now()
        self._conn.execute(
            "INSERT INTO apps (key, display, window_title, opens, "
            "created_at, last_used) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET opens = opens + ?, "
            "last_used = excluded.last_used, "
            "window_title = CASE WHEN excluded.window_title != '' "
            "THEN excluded.window_title ELSE apps.window_title END",
            (key, display[:60], (window_title or "").strip()[:90], opens,
             now, now, opens),
        )

    def note_open(self, name: str, window_title: str = "") -> str:
        key = app_key(name)
        if not key:
            return ""
        with self._lock:
            self._ensure_app(key, name.strip(), opens=1,
                             window_title=window_title)
            self._conn.commit()
        return key

    def note_control(self, app: str, name: str, action: str,
                     control_type: str = "", automation_id: str = "") -> None:
        key = app_key(app)
        name = (name or "").strip()
        if not key or not name or len(name) > 80:
            return
        with self._lock:
            self._ensure_app(key, app.strip())
            self._conn.execute(
                "INSERT INTO app_controls (app_key, name, action, "
                "control_type, automation_id, uses, last_used) "
                "VALUES (?, ?, ?, ?, ?, 1, ?) "
                "ON CONFLICT(app_key, name, action) DO UPDATE SET "
                "uses = uses + 1, last_used = excluded.last_used",
                (key, name, action, control_type, automation_id, _now()),
            )
            self._conn.commit()

    def note(self, app: str, note: str, kind: str = "note") -> None:
        key = app_key(app)
        note = " ".join((note or "").split())[:240]
        if not key or len(note) < 8:
            return
        with self._lock:
            self._ensure_app(key, app.strip())
            self._conn.execute(
                "INSERT INTO app_notes (app_key, kind, note, uses, created_at) "
                "VALUES (?, ?, ?, 1, ?) "
                "ON CONFLICT(app_key, note) DO UPDATE SET uses = uses + 1",
                (key, kind, note, _now()),
            )
            self._conn.commit()

    # ------------------------------------------------------------- reads

    def known_apps(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key FROM apps ORDER BY opens DESC"
            ).fetchall()
        return [r["key"] for r in rows]

    def app(self, name: str) -> dict[str, Any] | None:
        key = app_key(name)
        if not key:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM apps WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            controls = self._conn.execute(
                "SELECT name, action, control_type, automation_id, uses "
                "FROM app_controls WHERE app_key = ? "
                "ORDER BY uses DESC, last_used DESC LIMIT 14",
                (key,),
            ).fetchall()
            notes = self._conn.execute(
                "SELECT note, kind, uses FROM app_notes WHERE app_key = ? "
                "ORDER BY uses DESC, id DESC LIMIT 6",
                (key,),
            ).fetchall()
        return {
            **dict(row),
            "controls": [dict(c) for c in controls],
            "notes": [dict(n) for n in notes],
        }

    def profile(self, name: str) -> str:
        """A compact prompt block describing what Alfred knows about an app."""
        data = self.app(name)
        if not data:
            return ""

        lines = [f"{data['display']} (opened {data['opens']}x)"]
        if data["window_title"]:
            lines.append(f"  window title: {data['window_title']!r}")

        by_action: dict[str, list[str]] = {}
        for c in data["controls"]:
            label = c["name"]
            if c["control_type"]:
                label += f" [{c['control_type']}]"
            by_action.setdefault(c["action"], []).append(label)
        for action, names in by_action.items():
            lines.append(f"  {action}: " + ", ".join(names[:8]))

        for n in data["notes"]:
            lines.append(f"  note: {n['note']}")

        return "\n".join(lines)

    def profiles_for(self, text: str, limit: int = 2) -> str:
        """Profiles for every known app mentioned in ``text``."""
        low = f" {(text or '').lower()} "
        hits: list[tuple[int, str]] = []
        for key in self.known_apps():
            if not key:
                continue
            if re.search(rf"\b{re.escape(key)}\b", low):
                hits.append((len(key), key))
        hits.sort(reverse=True)
        blocks = [self.profile(k) for _, k in hits[:limit]]
        return "\n".join(b for b in blocks if b)

    # ---------------------------------------------------------- learning

    def learn_from_steps(self, steps: Iterable[Any]) -> int:
        """Record what worked from a finished task's tool steps.

        Only successful, auto-approved steps are learned from, so a
        refused or failed call never teaches Alfred a bad habit.
        """
        learned = 0
        current_app = ""
        for step in steps:
            tool = getattr(step, "tool", None)
            args = getattr(step, "args", None) or {}
            ok = bool(getattr(step, "ok", False))
            verdict = getattr(step, "verdict", "")
            if not ok or verdict != "auto" or not isinstance(args, dict):
                continue

            result = getattr(step, "result", None)
            title = ""
            if isinstance(result, dict):
                title = str(result.get("window_title")
                            or result.get("window") or "")

            if tool == "open_app":
                app = str(
                    args.get("app") or args.get("name")
                    or args.get("application") or ""
                )
                if app:
                    current_app = app
                    self.note_open(app, title)
                    learned += 1
                continue

            if tool != "ui_control":
                continue

            window = str(args.get("window") or "")
            app = window or current_app
            if not app:
                continue
            if window:
                current_app = window

            # Any successful interaction confirms this app is workable and
            # tells us its real window title - worth recording even when
            # the executor addressed controls by ref rather than by name.
            if title and title.strip().lower() != app.strip().lower():
                self.note_open(app, title)
                learned += 1

            action = str(args.get("action") or "")
            target = str(args.get("name") or args.get("item") or "")
            if action in ("click", "double_click", "right_click", "invoke",
                          "type", "get", "select", "expand") and target:
                self.note_control(app, target, action)
                learned += 1
            elif action == "key" and args.get("keys"):
                self.note(
                    app, f"keyboard shortcut {args['keys']} works here",
                    kind="quirk",
                )
                learned += 1
        return learned

    def close(self) -> None:
        with self._lock:
            self._conn.close()
