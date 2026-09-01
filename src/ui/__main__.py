"""
python -m src.ui [URL]  -  draw Alfred's interface.

With a URL, this is the window process Alfred spawns; it draws that
page and nothing else.

With no URL it runs the whole thing standalone: server and window
together, reading the stores directly. Useful when Alfred is not
running and you want to look at what it believes anyway - which is
exactly when you most want to.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    from src.ui.window import run

    if argv and argv[0].startswith("http"):
        return run(argv[0])

    # Standalone: bring up a server of our own first.
    from src.ui.server import INTERFACE

    url = INTERFACE.start()
    print(f"Interface on {url}")
    return run(url)


if __name__ == "__main__":
    raise SystemExit(main())
