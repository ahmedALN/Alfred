"""Alfred's interface: optional, on demand, and on loopback only.

Nothing heavy is imported here. The live hooks are published to from
the voice loop and the task queue, and those must not have to drag in
a web framework to say "Alfred is speaking now".
"""

from src.ui.live import BUS, LIVE, capture_output

__all__ = ["BUS", "INTERFACE", "LIVE", "Interface", "capture_output"]


def __getattr__(name: str):
    """The server, only if somebody actually asks for it."""
    if name in ("INTERFACE", "Interface"):
        from src.ui import server

        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
