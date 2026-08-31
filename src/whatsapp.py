"""
python -m src.whatsapp  -  link Alfred to your own WhatsApp.

    pair        link this machine as a device on your account
    status      whether a link already exists
    unlink      forget the link (and revoke it in WhatsApp too)

This is the arrangement where Alfred messages your own chat - the one
you get by messaging yourself. No business account and nothing exposed
to the internet: Alfred connects outward, the same way WhatsApp Web
does.

Worth knowing before you do it: this is not Meta's supported route for
programs, and WhatsApp's terms do not permit unofficial clients.
Accounts have been banned for it. The other route Alfred supports - the
official Cloud API, docs/whatsapp.md - carries no such risk but needs a
separate business number. Your call which you want.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SESSION = _ROOT / os.getenv("ALFRED_WHATSAPP_SESSION", "alfred_whatsapp.sqlite3")


def _number(argv: list[str]) -> str:
    if argv:
        return argv[0]

    from src.config import load_settings

    allowed = load_settings().whatsapp_allowed
    return allowed[0] if allowed else ""


def cmd_pair(argv: list[str]) -> int:
    number = _number(argv)
    if not number:
        print("Which number? Either pass it:")
        print("    python -m src.whatsapp pair +447700900123")
        print("or set ALFRED_WHATSAPP_ALLOWED in .env first.")
        return 1

    from src.messaging.whatsapp_personal import PersonalWhatsApp

    channel = PersonalWhatsApp(_SESSION, number)

    print(f"Linking Alfred to {number}.")
    print("Asking WhatsApp for a code...\n")

    try:
        code = channel.pair()
    except Exception as exc:  # noqa: BLE001
        print(f"Could not start pairing: {exc}")
        return 2

    print("Enter this in WhatsApp within about a minute - it expires.
")

    print(f"    YOUR CODE:  {code}\n")
    print("On your phone: WhatsApp -> Settings -> Linked Devices ->")
    print("Link a device -> Link with phone number instead -> type that code.")
    print("\nWaiting for you to enter it (Ctrl+C to give up)...")

    linked = {"done": False}

    def _noticed(message):
        linked["done"] = True

    channel.start(_noticed)

    try:
        for _ in range(120):          # two minutes is plenty
            if channel.connected:
                print("\nLinked. Alfred can now message your own chat.")
                print("Send it something to check.")
                return 0
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nGave up waiting.")
        return 1

    print("\nNo link after two minutes. The code expires - run pair again.")
    return 1


def cmd_status(_argv: list[str]) -> int:
    if _SESSION.exists():
        size = _SESSION.stat().st_size
        print(f"Linked. Session at {_SESSION.name} ({size // 1024} KB).")
        print("Alfred will connect to it at startup.")
    else:
        print("Not linked. Run: python -m src.whatsapp pair +447700900123")
    return 0


def cmd_unlink(_argv: list[str]) -> int:
    if not _SESSION.exists():
        print("Nothing to unlink.")
        return 0

    _SESSION.unlink()
    print(f"Forgot the local session ({_SESSION.name}).")
    print(
        "Also remove it on your phone: WhatsApp -> Linked Devices -> "
        "log the device out. Deleting the file here does not revoke it "
        "there."
    )
    return 0


_COMMANDS = {"pair": cmd_pair, "status": cmd_status, "unlink": cmd_unlink}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "status"

    if command not in _COMMANDS:
        print(__doc__)
        return 1

    return _COMMANDS[command](argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
