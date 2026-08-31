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

import os
import threading
import time
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
        self._starting = threading.Lock()
        # What Alfred has just said, so it does not reply to itself.
        self._echoes: deque[str] = deque(maxlen=40)
        self.connected = False
        self.displaced = False
        self._stopping = False
        self.retry_wait = 2.0

    @property
    def configured(self) -> bool:
        return bool(self._owner)

    # ------------------------------------------------------------- plumbing

    def _build(self) -> Any:
        if self._client is not None:
            return self._client

        from neonize.client import NewClient
        from neonize.events import (
            ConnectedEv,
            DisconnectedEv,
            LoggedOutEv,
            MessageEv,
            StreamReplacedEv,
        )

        _quieten()
        client = NewClient(self._session)

        @client.event(ConnectedEv)
        def _connected(_cli: Any, _event: Any) -> None:
            self.connected = True
            print("[WhatsApp] linked and listening.")

        # WhatsApp allows one live connection per linked device. A
        # second one silently unseats the first, and the first goes deaf
        # without any sign it has - which looks exactly like Alfred
        # ignoring you. So say so.
        @client.event(StreamReplacedEv)
        def _replaced(_cli: Any, _event: Any) -> None:
            self.connected = False
            self.displaced = True
            print(
                "[WhatsApp] something else took the link - probably "
                "another Alfred, or 'python -m src.whatsapp pair' still "
                "running. This one has stopped listening."
            )

        @client.event(LoggedOutEv)
        def _logged_out(_cli: Any, _event: Any) -> None:
            self.connected = False
            print(
                "[WhatsApp] the device was logged out on your phone. "
                "Pair again: python -m src.whatsapp pair"
            )

        @client.event(DisconnectedEv)
        def _disconnected(_cli: Any, _event: Any) -> None:
            self.connected = False

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
        # A photo sent with no caption is not an empty message. It is
        # the commonest way anybody shows anybody anything.
        if not text and not _kind_of(event):
            return

        info = getattr(event, "Info", None)
        source = getattr(info, "MessageSource", None)
        sender = getattr(getattr(source, "Sender", None), "User", "") or ""
        chat = getattr(getattr(source, "Chat", None), "User", "") or ""
        from_me = bool(getattr(source, "IsFromMe", False))

        # A linked device sees the whole account - every chat, every
        # contact. Only one of them is an instruction to Alfred, and
        # everything else is private correspondence it has no business
        # reading, let alone obeying. Filter here, before a word of it
        # reaches anything that acts.
        if not _own_chat(sender, chat, from_me):
            return

        # In your own chat every message is "from me", including the
        # ones Alfred just sent. Answering those is an endless
        # conversation with itself.
        with self._lock:
            if text.strip() in self._echoes:
                self._echoes.remove(text.strip())
                return

        if self._on_message is None:
            return

        kind = _kind_of(event)
        media = self._fetch(event) if kind else None

        # Report it as the owner's number rather than whatever WhatsApp
        # addressed it with. It is not a guess: reaching this line means
        # the message came from the very account this device is linked
        # to, which is a stronger proof of who sent it than any number
        # in the envelope. The number is what the rest of Alfred - the
        # allowlist, the replies - is written in terms of.
        self._on_message(
            Inbound(
                sender=self._owner, text=text, channel=self.name, raw=event,
                media=media, media_kind=kind,
            )
        )

    # -------------------------------------------------------------- lifecycle

    def _connect(self) -> None:
        """Get the connection going, once, in the background.

        The library's connect() does not return until the link is torn
        down - it is the whole session, not a handshake - so it can only
        ever live on its own thread.
        """
        with self._starting:
            if self._thread is not None and self._thread.is_alive():
                return

            client = self._build()
            self._stopping = False

            def run() -> None:
                """Redial a dropped line, but not one someone else holds.

                A connection that simply dies - the laptop slept, the
                wifi went - should come back on its own; being deaf
                until somebody notices is the whole problem. A
                connection taken by another program is different:
                reconnecting there is two Alfreds unseating each other
                for ever, so that one is reported and left alone.
                """
                wait = self.retry_wait
                while not self._stopping:
                    self.displaced = False
                    try:
                        client.connect()
                    except Exception as exc:  # noqa: BLE001
                        print(f"[WhatsApp] connection ended: {exc}")
                    finally:
                        self.connected = False

                    if self._stopping or self.displaced:
                        return
                    print(f"[WhatsApp] dropped - reconnecting in {wait:.0f}s.")
                    time.sleep(wait)
                    wait = min(wait * 2, 60.0)

            self._thread = threading.Thread(
                target=run, name="alfred-whatsapp", daemon=True
            )
            self._thread.start()

    def pair(self, show_notification: bool = True, timeout: float = 30.0) -> str:
        """Ask WhatsApp for the code you type into Linked Devices.

        A code can only be asked for over a live connection, and the
        connection is only up once the background thread above has got
        going. Until then the request comes back "client is nil", which
        is not a failure - it is being early. So: start connecting, then
        keep asking until it takes.
        """
        client = self._build()
        client.qr(_no_qr)          # we are pairing by code, not by camera
        self._connect()

        deadline = time.monotonic() + timeout
        while True:
            try:
                return client.PairPhone(self._owner, show_notification)
            except Exception as exc:  # noqa: BLE001
                if not _still_waking(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.4)

    def start(self, on_message: Callable[[Inbound], None]) -> None:
        self._on_message = on_message
        self._connect()

    def stop(self) -> None:
        """Let go, properly.

        disconnect() closes the socket but leaves the library's own
        worker alive, and that worker is not a daemon - a program that
        only disconnects hangs at exit. stop() cancels it, so ask for
        that first and fall back to closing the socket.
        """
        self._stopping = True
        client = self._client
        if client is not None:
            for release in ("stop", "disconnect"):
                try:
                    getattr(client, release)()
                    break
                except Exception:  # noqa: BLE001
                    continue
        self.connected = False

        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(5)

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

    def _fetch(self, event: Any) -> bytes | None:
        """The picture itself, off WhatsApp's servers.

        Encrypted in transit and fetched by the same linked device that
        was allowed to see it in the first place, so nothing new is
        being reached into.
        """
        try:
            client = self._build()
            data = client.download_any(event.Message)
            return bytes(data) if data else None
        except Exception as exc:  # noqa: BLE001
            print(f"[WhatsApp] could not fetch what you sent: {exc}")
            return None

    def send_file(
        self, data: bytes | str, kind: str = "image",
        caption: str = "", to: str | None = None,
    ) -> bool:
        """A picture, or a clip of the screen, into the same chat.

        Sent as a real WhatsApp image or video rather than a file, so it
        shows in the conversation instead of asking to be downloaded -
        which is the whole point of asking for it from a phone.
        """
        try:
            from neonize.utils import build_jid

            client = self._build()
            number = "".join(
                c for c in str(to or self._owner) if c.isdigit()
            ) or self._owner
            where = build_jid(number)

            if kind == "video":
                client.send_video(where, data, caption=caption or None)
            else:
                client.send_image(where, data, caption=caption or None)
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[WhatsApp] could not send the {kind}: {exc}")
            return False


def _own_chat(sender: str, chat: str, from_me: bool) -> bool:
    """Is this me, talking to myself?

    Not a question about phone numbers. WhatsApp increasingly addresses
    people by a per-account "LID" instead - the messages that went
    unanswered were addressed 271712549638356, nothing like the number
    they came from - so matching digits does not work and will keep not
    working.

    What does work is the shape of the thing. "From me" can only be
    produced by the account this device is linked to; sender and chat
    being the same means that account is the other end as well. Both at
    once is the self-chat and nothing else: a message to a friend has a
    different chat, a message from a friend is not from me.
    """
    return bool(from_me and sender and sender == chat)


def _quieten() -> None:
    """WhatsApp's own logs are a wall of prekeys and history sync.

    The library hands its Python log level down to the Go side, so
    turning it down here turns it down there too, at the source, rather
    than filtering a flood after the fact.
    """
    import logging

    level = os.getenv("ALFRED_WHATSAPP_LOG", "WARNING").upper()
    for name in ("neonize", "neonize.utils.log", "neonize.client",
                 "whatsmeow", "whatsmeow.Client", "Whatsmeow.Database"):
        logging.getLogger(name).setLevel(level)


def _no_qr(*_args: Any) -> None:
    """Swallow the QR image. Pairing by code has no use for it."""


def _still_waking(exc: Exception) -> bool:
    """Is this "not up yet" rather than "no"?"""
    return "client is nil" in str(exc).lower()


def _text_of(event: Any) -> str:
    """The words out of whichever kind of message this is.

    A photo's words are its caption, and often there are none - "what
    is this?" is a perfectly good message when the picture says the
    rest.
    """
    message = getattr(event, "Message", None)
    if message is None:
        return ""

    plain = getattr(message, "conversation", "") or ""
    if plain:
        return plain

    extended = getattr(message, "extendedTextMessage", None)
    if extended is not None:
        text = getattr(extended, "text", "") or ""
        if text:
            return text

    for field in ("imageMessage", "videoMessage", "documentMessage"):
        part = getattr(message, field, None)
        if part is not None and getattr(part, "url", ""):
            return (getattr(part, "caption", "") or "").strip()

    return ""


def _kind_of(event: Any) -> str:
    """Whether something was attached, and what sort of thing."""
    message = getattr(event, "Message", None)
    if message is None:
        return ""

    for field, kind in (
        ("imageMessage", "image"),
        ("videoMessage", "video"),
        ("documentMessage", "document"),
    ):
        part = getattr(message, field, None)
        # url is what separates a real attachment from an empty
        # sub-message the protobuf always carries.
        if part is not None and getattr(part, "url", ""):
            return kind
    return ""
