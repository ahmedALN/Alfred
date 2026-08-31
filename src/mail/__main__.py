"""
python -m src.mail  -  let Alfred read your inbox.

    link      connect a Gmail account (opens a browser once)
    status    which mailbox, and what Alfred may do with it
    unlink    forget it here

What Alfred is given, and cannot exceed:

    read messages, search, mark read, archive
    write drafts

    NOT send. NOT delete.

That is not a promise about how carefully this was written. Alfred asks
Google for the gmail.modify permission, which does not include sending,
so the send endpoint refuses it. You can withdraw the whole thing at
myaccount.google.com/permissions without touching this machine.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent


def _mail():
    from src.mail import Gmail

    return Gmail(
        secrets=_ROOT / os.getenv("ALFRED_GMAIL_SECRETS", "gmail_client.json"),
        token=_ROOT / os.getenv("ALFRED_GMAIL_TOKEN", "gmail_token.json"),
    )


def cmd_link(_argv: list[str]) -> int:
    from src.mail import MailError

    mail = _mail()
    print("A browser will open on Google's own consent page.")
    print("Alfred is asking to read, sort and draft - not to send.\n")

    try:
        address = mail.link()
    except MailError as exc:
        print(f"Could not link: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"Could not link: {type(exc).__name__}: {exc}")
        return 2

    print(f"\nLinked to {address}.")
    print("Alfred can now read it, sort it, and leave you drafts.")
    print("Take it back any time at myaccount.google.com/permissions.")
    return 0


def cmd_status(_argv: list[str]) -> int:
    mail = _mail()
    if not mail.linked:
        print("No mailbox linked. Run: python -m src.mail link")
        return 0

    try:
        address = mail.address(refresh=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Linked, but the connection is not working: {exc}")
        return 1

    print(f"Linked to {address}.")
    print("Alfred may:     read, search, mark read, archive, draft")
    print("Alfred may not: send, delete - Google refuses, not just Alfred")
    return 0


def cmd_unlink(_argv: list[str]) -> int:
    if _mail().unlink():
        print("Forgot the mailbox here.")
        print("To revoke it properly as well: myaccount.google.com/permissions")
    else:
        print("Nothing linked.")
    return 0


_COMMANDS = {"link": cmd_link, "status": cmd_status, "unlink": cmd_unlink}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "status"

    if command not in _COMMANDS:
        print(__doc__)
        return 1
    return _COMMANDS[command](argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
