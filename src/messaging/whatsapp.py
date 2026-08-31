"""WhatsApp, over Meta's official Cloud API.

The unofficial libraries that drive WhatsApp Web are easier to set up
and get personal numbers banned, so this uses the supported route even
though it needs an account and a business number.

Two things have to be true before a message is acted on: Meta signed it,
and the sender is on the allowlist. The signature proves the request
came from WhatsApp rather than from anyone who found the address; the
allowlist proves it came from the owner rather than from anyone who
found the bot. Neither is sufficient alone.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Callable

from src.messaging.base import Channel, Inbound

_GRAPH = "https://graph.facebook.com/v21.0"


class WhatsAppChannel(Channel):
    name = "whatsapp"

    def __init__(
        self,
        token: str,
        phone_number_id: str,
        *,
        app_secret: str = "",
        verify_token: str = "",
    ) -> None:
        self._token = (token or "").strip()
        self._phone_id = (phone_number_id or "").strip()
        self._app_secret = (app_secret or "").strip()
        self._verify_token = (verify_token or "").strip()
        self._on_message: Callable[[Inbound], None] | None = None

    @property
    def configured(self) -> bool:
        return bool(self._token and self._phone_id)

    # ---------------------------------------------------------------- inbound

    def start(self, on_message: Callable[[Inbound], None]) -> None:
        self._on_message = on_message

    def verify_subscription(self, mode: str, token: str,
                            challenge: str) -> str | None:
        """Meta's one-time check that we own this address."""
        if mode == "subscribe" and token and token == self._verify_token:
            return challenge
        return None

    def signature_ok(self, body: bytes, header: str) -> bool:
        """Did this really come from Meta?

        Without an app secret there is nothing to check against, and a
        webhook that cannot tell Meta from a stranger must not be
        trusted with a machine - so an unsigned setup refuses everything
        rather than accepting everything.
        """
        if not self._app_secret:
            return False

        header = (header or "").strip()
        if not header.startswith("sha256="):
            return False

        expected = hmac.new(
            self._app_secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(expected, header[len("sha256="):])

    def parse(self, body: bytes) -> list[Inbound]:
        """Pull the human-written messages out of a webhook payload."""
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return []

        found: list[Inbound] = []

        for entry in payload.get("entry", []) or []:
            for change in entry.get("changes", []) or []:
                value = change.get("value") or {}
                for message in value.get("messages", []) or []:
                    # Only text. A voice note or an image is not
                    # something to act on blindly.
                    if message.get("type") != "text":
                        continue
                    text = ((message.get("text") or {}).get("body") or "")
                    sender = message.get("from") or ""
                    if not sender or not text.strip():
                        continue
                    found.append(
                        Inbound(sender=sender, text=text,
                                channel=self.name, raw=message)
                    )

        return found

    def deliver(self, body: bytes, signature: str) -> int:
        """Hand a verified payload to whoever is listening."""
        if not self.signature_ok(body, signature):
            print("[WhatsApp] refused a payload with a bad signature")
            return 0

        if self._on_message is None:
            return 0

        messages = self.parse(body)
        for message in messages:
            try:
                self._on_message(message)
            except Exception as exc:  # noqa: BLE001
                print(f"[WhatsApp] handler failed: {exc}")

        return len(messages)

    # --------------------------------------------------------------- outbound

    def send(self, text: str, to: str | None = None) -> bool:
        if not self.configured or not to or not (text or "").strip():
            return False

        try:
            import httpx

            response = httpx.post(
                f"{_GRAPH}/{self._phone_id}/messages",
                headers={"Authorization": f"Bearer {self._token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": _wire_number(to),
                    "type": "text",
                    # WhatsApp caps a message body; a long report is
                    # better trimmed than rejected outright.
                    "text": {"body": text[:4000]},
                },
                timeout=20.0,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[WhatsApp] send failed: {exc}")
            return False

        if response.status_code >= 400:
            detail = response.text[:200]
            if "re-engagement" in detail or "24" in detail:
                print(
                    "[WhatsApp] outside the 24-hour window - WhatsApp only "
                    "allows free-form messages within a day of your last "
                    "one. Send anything to reopen it."
                )
            else:
                print(f"[WhatsApp] send refused: {detail}")
            return False

        return True


def _wire_number(value: str) -> str:
    """WhatsApp wants digits only, with the country code, no plus."""
    digits = "".join(c for c in str(value) if c.isdigit())
    return digits
