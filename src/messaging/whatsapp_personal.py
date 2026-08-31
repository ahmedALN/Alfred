"""WhatsApp as a linked device, talking to your own chat.

This is the arrangement where you give Alfred your number and its
messages arrive in your own "Message yourself" chat - no business
account, no separate sender, no tunnel, nothing exposed to the internet.
It works by linking as a companion device, the same way WhatsApp Web
does: you approve it once from Linked Devices and the session is kept on
disk afterwards.

The trade, stated plainly because it is your account: this is not
Meta's supported route for programs. It is the same mechanism every
"WhatsApp bot" uses, and WhatsApp's terms do not permit unofficial
clients. Accounts have been banned for it. The official Cloud API in
whatsapp.py has none of that risk and needs a business number instead;
both are wired up, and this one is off unless you turn it on.

The other thing to know: your own chat is the one place where Alfred's
replies come back to Alfred as new messages. Everything sent is
remembered for a moment so it is not answered as though you had said it.
"""

from __future__ import annotations

import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable

from src.messaging.base import Channel, Inbound


class PersonalWhatsApp(Channel):
    name = "whatsapp"

    def __init__(self, session: str | Path, owner: str) -> None:
        self._session = str(session)
        self._owner = "".join(c for c in str(owner) if c.isdigit())
        self._client: Any = None
        self._on_message: Callable[[Inbound], None] | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # What Alfred has just said, so it does not reply to itself.
        self._echoes: deque[str] = deque(maxlen=40)
        self.connected = False

    @property
    def configured(self) -> bool:
        return bool(self._owner)

    # ------------------------------------------------------------- plumbing

    def _build(self) -> Any:
        if self._client is not None:
            return self._client

        from neonize.client import NewClient
        from neonize.events import ConnectedEv, MessageEv

        client = NewClient(self._session)

        @client.event(ConnectedEv)
        def _connected(_cli: Any, _event: Any) -> None:
            self.connected = True
            print("[WhatsApp] linked and listening.")

        @client.event(MessageEv)
        def _message(_cli: Any, event: Any) -> None:
            try:
                self._deliver(event)
            except Exception as exc:  # noqa: BLE001
                print(f"[WhatsApp] could not read a message: {exc}")

        self._client = client
        return client

    def _deliver(self, event: Any) -> None:
        text = _text_of(event)
        if not text:
            return

        info = getattr(event, "Info", None)
        source = getattr(info, "MessageSource", None)
        sender = getattr(getattr(source, "Sender", None), "User", "") or ""

        # In your own chat every message is "from me", including the
        # ones Alfred just sent. Answering those is an endless
        # conversation with itself.
        with self._lock:
            if text.strip() in self._echoes:
                self._echoes.remove(text.strip())
                return

        if self._on_message is None:
            return

        self._on_message(
            Inbound(sender=sender, text=text, channel=self.name, raw=event)
        )

    # -------------------------------------------------------------- lifecycle

    def pair(self, show_notification: bool = True) -> str:
        """Ask WhatsApp for the code you type into Linked Devices."""
        client = self._build()
        return client.PairPhone(self._owner, show_notification)

    def start(self, on_message: Callable[[Inbound], None]) -> None:
        self._on_message = on_message
        client = self._build()

        def run() -> None:
            try:
                client.connect()
            except Exception as exc:  # noqa: BLE001
                print(f"[WhatsApp] connection ended: {exc}")
            finally:
                self.connected = False

        self._thread = threading.Thread(
            target=run, name="alfred-whatsapp", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self.connected = False

    # --------------------------------------------------------------- sending

    def send(self, text: str, to: str | None = None) -> bool:
        text = (text or "").strip()
        if not text:
            return False

        try:
            from neonize.utils import build_jid

            client = self._build()
            number = "".join(
                c for c in str(to or self._owner) if c.isdigit()
            ) or self._owner

            with self._lock:
                self._echoes.append(text[:4000].strip())

            client.send_message(build_jid(number), text[:4000])
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[WhatsApp] send failed: {exc}")
            with self._lock:
                if text[:4000].strip() in self._echoes:
                    self._echoes.remove(text[:4000].strip())
            return False


def _text_of(event: Any) -> str:
    """The words out of whichever kind of message this is."""
    message = getattr(event, "Message", None)
    if message is None:
        return ""

    plain = getattr(message, "conversation", "") or ""
    if plain:
        return plain

    extended = getattr(message, "extendedTextMessage", None)
    return (getattr(extended, "text", "") or "") if extended else ""
