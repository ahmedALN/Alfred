"""Reading the inbox, and writing drafts that only you can send."""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

# Read, label, archive, draft. Not send. Not delete.
#
# No scope grants drafts without also granting send - gmail.compose does
# both - so drafts are made through modify, which does not carry send at
# all. If Alfred is ever asked to send, Google refuses before Alfred has
# to decide not to.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

_QUOTE = re.compile(r"^\s*(>|On .{0,80} wrote:)", re.M)


class MailError(RuntimeError):
    pass


class Gmail:
    def __init__(
        self,
        secrets: Path | str,
        token: Path | str,
        address: str = "",
    ) -> None:
        self._secrets = Path(secrets)
        self._token = Path(token)
        self._address = address
        self._service: Any = None

    # ------------------------------------------------------------ access

    @property
    def linked(self) -> bool:
        return self._token.exists()

    def link(self) -> str:
        """Open a browser once so the account's owner can say yes.

        The consent happens on Google's own page. Nothing here ever sees
        a password, and the permission can be taken back from the
        account's security settings without touching this machine.
        """
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not self._secrets.exists():
            raise MailError(
                self._secrets.name + " is missing - see docs/mail.md for "
                "the few minutes of Google setup it needs."
            )

        flow = InstalledAppFlow.from_client_secrets_file(
            str(self._secrets), SCOPES
        )
        creds = flow.run_local_server(port=0, prompt="consent")
        self._token.write_text(creds.to_json(), encoding="utf-8")

        try:
            self._token.chmod(0o600)
        except OSError:
            pass

        self._service = None
        return self.address(refresh=True)

    def unlink(self) -> bool:
        if not self._token.exists():
            return False
        self._token.unlink()
        self._service = None
        return True

    def _api(self):
        if self._service is not None:
            return self._service

        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        if not self._token.exists():
            raise MailError(
                "not linked to a mailbox yet - run: python -m src.mail link"
            )

        creds = Credentials.from_authorized_user_file(str(self._token), SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self._token.write_text(creds.to_json(), encoding="utf-8")
            else:
                raise MailError(
                    "the mailbox link has expired - run: "
                    "python -m src.mail link"
                )

        self._service = build(
            "gmail", "v1", credentials=creds, cache_discovery=False
        )
        return self._service

    def address(self, refresh: bool = False) -> str:
        if self._address and not refresh:
            return self._address
        profile = self._api().users().getProfile(userId="me").execute()
        self._address = profile.get("emailAddress", "")
        return self._address

    # ------------------------------------------------------------ reading

    def search(self, query: str = "", limit: int = 10) -> list[dict[str, Any]]:
        """Gmail's own search, so "from:mum is:unread" works as written."""
        api = self._api()
        listed = api.users().messages().list(
            userId="me",
            q=query or "in:inbox",
            maxResults=max(1, min(int(limit), 50)),
        ).execute()

        return [
            self.read(stub["id"], body=False)
            for stub in (listed.get("messages") or [])
        ]

    def unread(self, limit: int = 10) -> list[dict[str, Any]]:
        return self.search("is:unread in:inbox", limit)

    def read(self, message_id: str, body: bool = True) -> dict[str, Any]:
        api = self._api()
        msg = api.users().messages().get(
            userId="me",
            id=message_id,
            format="full" if body else "metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()

        headers = {
            h["name"].lower(): h["value"]
            for h in (msg.get("payload", {}).get("headers") or [])
        }
        out = {
            "id": msg["id"],
            "thread": msg.get("threadId", ""),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "subject": headers.get("subject", "(no subject)"),
            "when": _when(msg.get("internalDate")),
            "unread": "UNREAD" in (msg.get("labelIds") or []),
            "snippet": msg.get("snippet", ""),
        }
        if body:
            out["body"] = _body(msg.get("payload", {}))
        return out

    # ------------------------------------------------------------ sorting

    def archive(self, message_id: str) -> bool:
        """Out of the inbox, not out of existence."""
        self._api().users().messages().modify(
            userId="me", id=message_id, body={"removeLabelIds": ["INBOX"]}
        ).execute()
        return True

    def mark_read(self, message_id: str) -> bool:
        self._api().users().messages().modify(
            userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
        ).execute()
        return True

    # ------------------------------------------------------------ writing

    def draft(
        self,
        to: str,
        subject: str,
        body: str,
        reply_to: str = "",
    ) -> dict[str, Any]:
        """Write it and leave it in Drafts. Sending is yours."""
        api = self._api()

        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        if self._address:
            message["From"] = self._address

        payload: dict[str, Any] = {
            "message": {
                "raw": base64.urlsafe_b64encode(message.as_bytes()).decode()
            }
        }
        if reply_to:
            original = api.users().messages().get(
                userId="me", id=reply_to, format="metadata",
                metadataHeaders=["Subject"],
            ).execute()
            payload["message"]["threadId"] = original.get("threadId")

        created = api.users().drafts().create(
            userId="me", body=payload
        ).execute()

        return {
            "draft": created.get("id", ""),
            "to": to,
            "subject": subject,
            "where": "in your Drafts - nothing has been sent",
        }


# ------------------------------------------------------------- helpers


def _when(internal: Any) -> str:
    try:
        stamp = datetime.fromtimestamp(int(internal) / 1000, tz=timezone.utc)
        return stamp.astimezone().strftime("%d %b %H:%M")
    except Exception:  # noqa: BLE001
        return ""


def _body(payload: dict[str, Any], limit: int = 4000) -> str:
    """The words, with the quoted reply chain cut off.

    A thread of six replies is mostly the previous five. Handing all of
    it to a model to summarise costs a great deal and says the same
    thing as handing it the top.
    """
    text = _find_text(payload)
    if _QUOTE.search(text):
        text = _QUOTE.split(text)[0]
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _find_text(payload: dict[str, Any]) -> str:
    mime = payload.get("mimeType", "")
    data = (payload.get("body") or {}).get("data")

    if mime == "text/plain" and data:
        return _decode(data)

    for part in payload.get("parts") or []:
        found = _find_text(part)
        if found:
            return found

    if mime == "text/html" and data:
        return _strip_html(_decode(data))
    return ""


def _decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>", "\n", html)
    html = re.sub(r"<[^>]+>", " ", html)
    for entity, plain in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
        ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"),
    ):
        html = html.replace(entity, plain)
    return re.sub(r"[ \t]{2,}", " ", html)
