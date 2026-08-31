"""
python -m src.workspace  -  connect Alfred to your Google account.

    link      sign in (opens a browser once)
    status    which account, and what Alfred may do with it
    unlink    forget it here

One sign-in covers everything. What Alfred asks for:

    Gmail       read, search, label, archive, draft      NOT send
    Calendar    read events, add events                  not delete
    Classroom   courses, coursework, due dates           read only

The Gmail line is kept by Google - the permission Alfred holds has no
send in it, so the send endpoint refuses it. The Calendar line is kept
by Alfred's own code, because Google has no permission that grants
"add events" without also granting "delete events". That is a weaker
promise and is said as one.

Withdraw the lot at myaccount.google.com/permissions, any time, without
touching this machine.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent


def _account():
    from src.workspace.account import GoogleAccount

    return GoogleAccount(
        secrets=_ROOT / os.getenv("ALFRED_GMAIL_SECRETS", "gmail_client.json"),
        token=_ROOT / os.getenv("ALFRED_GMAIL_TOKEN", "gmail_token.json"),
    )


def cmd_link(_argv: list[str]) -> int:
    from src.workspace.account import GoogleError

    account = _account()
    print("A browser will open on Google's own consent page.")
    print("Alfred is asking to read your mail, calendar and coursework,")
    print("to draft replies, and to add calendar events.")
    print("It is NOT asking to send mail or to delete anything.\n")

    try:
        address = account.link()
    except GoogleError as exc:
        print(f"Could not link: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"Could not link: {type(exc).__name__}: {exc}")
        return 2

    print(f"\nLinked to {address}.")
    print("Take it back any time at myaccount.google.com/permissions.")
    return 0


def cmd_status(_argv: list[str]) -> int:
    account = _account()
    if not account.linked:
        print("No Google account linked. Run: python -m src.workspace link")
        return 0

    short = account.missing()
    if short:
        print("Linked, but the sign-in predates some of what Alfred now")
        print(f"needs ({len(short)} permission(s)). Run link again.")
        return 1

    try:
        address = account.address(refresh=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Linked, but not working: {exc}")
        return 1

    print(f"Linked to {address}.\n")
    print("  Gmail       read, search, archive, draft     not send")
    print("  Calendar    read, add events                 not delete")
    print("  Classroom   courses, coursework, due dates   read only")
    return 0


def cmd_unlink(_argv: list[str]) -> int:
    if _account().unlink():
        print("Forgot the Google account here.")
        print("To revoke it properly: myaccount.google.com/permissions")
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
