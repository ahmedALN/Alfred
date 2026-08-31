"""Reading the inbox, and writing drafts that only you can send."""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from src.workspace.account import GMAIL as SCOPES
from src.workspace.account import GoogleError

# Read, label, archive, draft. Not send. Not delete.
#
# No scope grants drafts without also granting send - gmail.compose does
# both - so drafts are made through modify, which does not carry send at
# all. If Alfred is ever asked to send, Google refuses before Alfred has
# to decide not to. The scope itself lives in workspace.account, with
# the others, because one sign-in covers all of them.

_QUOTE = re.compile(r"^\s*(>|On .{0,80} wrote:)", re.M)

# Kept as a name of its own so callers catching MailError still do; it
# is the same failure.
MailError = GoogleError


class Gmail:
    """The inbox, on the shared Google sign-in.

    This used to hold its own OAuth flow and its own token file. Adding
    the calendar that way would have meant a second browser consent for
    the same account, and Classroom a third.
    """

    def __init__(self, account: Any) -> None:
        self._account = account

    @property
    def linked(self) -> bool:
        return bool(getattr(self._account, "linked", False))

    def address(self, refresh: bool = False) -> str:
        return self._account.address(refresh=refresh)

    def _api(self):
        return self._account.service("gmail", "v1")

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
        mine = self.address()
        if mine:
            message["From"] = mine

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
