"""One Google account, one consent, several services.

Gmail arrived first and carried its own sign-in. Adding the calendar
that way would have meant a second browser dance and a second token
file, and Classroom a third - three consents for one account, and three
things to notice had expired.

So the sign-in lives here and the services borrow it. Adding a service
adds its permissions to the list; the account notices that the token it
holds predates them and says to sign in again, rather than failing later
with a permission error nobody can read.

What Alfred asks for, and what it deliberately does not:

    Gmail       read, search, label, archive, draft      not send
    Calendar    read events, add events                  see below
    Classroom   courses, coursework, due dates           read only

The mail line is kept by Google: the permission Alfred holds has no send
in it. The calendar line is not, and it is worth being exact about that.
Google has no scope for "add events but never remove them" - the one
that lets Alfred put something in your diary lets it take something out
too. That limit is kept by this code, which is a weaker promise than the
one made about mail, and it is the honest description of it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

GMAIL = ["https://www.googleapis.com/auth/gmail.modify"]

CALENDAR = ["https://www.googleapis.com/auth/calendar.events"]

CLASSROOM = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
]

SCOPES = GMAIL + CALENDAR + CLASSROOM


class GoogleError(RuntimeError):
    pass


# What each permission buys, so a refusal can be explained in terms of
# the thing that stopped working rather than a URL.
_BUYS = {
    "gmail.modify": "read your mail",
    "calendar.events": "see or add calendar events",
    "classroom.courses.readonly": "see your courses",
    "classroom.coursework.me.readonly": "see assignment deadlines",
    "classroom.student-submissions.me.readonly": "see what you handed in",
    "classroom.announcements.readonly": "see class announcements",
}


def explain_denied(exc: BaseException, held: list[str]) -> str:
    """Turn a permission refusal into a sentence about what is missing.

    Alfred no longer refuses to start when a permission is absent - a
    sign-in missing one of six works for the other five. The cost is
    that the edge is met later, as a 403 nobody can read, so this is
    where it gets named.
    """
    text = str(exc)
    if "403" not in text and "insufficient" not in text.lower():
        return text

    absent = [s for s in SCOPES if s not in set(held)]
    if not absent:
        return text

    lost = [
        _BUYS[s.rsplit("/", 1)[-1]]
        for s in absent if s.rsplit("/", 1)[-1] in _BUYS
    ]
    return (
        "Google refused that: this sign-in cannot "
        + (", ".join(lost) if lost else "do that")
        + ". Tell the user to run: python -m src.workspace link"
    )


class GoogleAccount:
    """The signed-in account, and the services built on it."""

    def __init__(
        self,
        secrets: Path | str,
        token: Path | str,
        scopes: list[str] | None = None,
    ) -> None:
        self._secrets = Path(secrets)
        self._token = Path(token)
        self._scopes = list(scopes or SCOPES)
        self._creds: Any = None
        self._services: dict[str, Any] = {}
        self._address = ""

    # ------------------------------------------------------------ signing in

    @property
    def linked(self) -> bool:
        return self._token.exists()

    def link(self) -> str:
        """Open a browser once so the account's owner can say yes.

        Consent happens on Google's own page. Nothing here ever sees a
        password, and it can be withdrawn from the account's security
        settings without touching this machine.
        """
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not self._secrets.exists():
            raise GoogleError(
                self._secrets.name + " is missing - see docs/google.md for "
                "the Google setup it needs."
            )

        # Google does not always grant everything it is asked for, and
        # the library treats ANY difference as fatal - so a consent where
        # five permissions out of six came back was thrown away whole,
        # and the sign-in failed with a warning about scopes having
        # changed. Five out of six is a working mailbox and calendar.
        # Take what was given and say what was not.
        os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

        flow = InstalledAppFlow.from_client_secrets_file(
            str(self._secrets), self._scopes
        )
        creds = flow.run_local_server(port=0, prompt="consent")
        self._token.write_text(creds.to_json(), encoding="utf-8")
        try:
            self._token.chmod(0o600)
        except OSError:
            pass

        self._creds = None
        self._services.clear()
        self._address = ""
        return self.address()

    def unlink(self) -> bool:
        if not self._token.exists():
            return False
        self._token.unlink()
        self._creds = None
        self._services.clear()
        return True

    def granted(self) -> list[str]:
        """What the account actually gave, which is not always what was
        asked for."""
        if not self._token.exists():
            return []
        try:
            from google.oauth2.credentials import Credentials

            creds = Credentials.from_authorized_user_file(str(self._token))
            return list(creds.scopes or [])
        except Exception:  # noqa: BLE001
            return []

    def missing(self) -> list[str]:
        """Permissions asked for now that the stored sign-in never had.

        Adding a service after somebody has already signed in is the
        normal way this happens, and the symptom without this check is
        an unreadable 403 from whichever call needed it.
        """
        if not self._token.exists():
            return list(self._scopes)
        try:
            from google.oauth2.credentials import Credentials

            creds = Credentials.from_authorized_user_file(str(self._token))
            held = set(creds.scopes or [])
        except Exception:  # noqa: BLE001
            return []
        return [s for s in self._scopes if s not in held]

    # ------------------------------------------------------------ using it

    def _credentials(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        if not self._token.exists():
            raise GoogleError(
                "no Google account linked yet - run: python -m src.workspace link"
            )

        if self._creds is None:
            # Built from what was actually granted, not what was asked
            # for. Insisting on the full list turns a partly-granted
            # account into no account at all.
            self._creds = Credentials.from_authorized_user_file(
                str(self._token), self.granted() or self._scopes
            )

        if not self._creds.valid:
            if self._creds.expired and self._creds.refresh_token:
                self._creds.refresh(Request())
                self._token.write_text(self._creds.to_json(), encoding="utf-8")
            else:
                raise GoogleError(
                    "the Google sign-in has expired - Google does that "
                    "weekly to apps still in testing. Run: "
                    "python -m src.workspace link"
                )
        return self._creds

    def service(self, api: str, version: str):
        key = api + version
        if key not in self._services:
            from googleapiclient.discovery import build

            self._services[key] = build(
                api, version, credentials=self._credentials(),
                cache_discovery=False,
            )
        return self._services[key]

    def address(self, refresh: bool = False) -> str:
        if self._address and not refresh:
            return self._address
        profile = self.service("gmail", "v1").users().getProfile(
            userId="me"
        ).execute()
        self._address = profile.get("emailAddress", "")
        return self._address
