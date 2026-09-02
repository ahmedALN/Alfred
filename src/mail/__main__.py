"""
python -m src.mail  -  kept working; the account now covers more than mail.

Everything moved to src.workspace when the calendar and Classroom
joined, because they share one sign-in. This forwards, so anything
written down before still runs.
"""

from __future__ import annotations

import sys

from src.workspace.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
