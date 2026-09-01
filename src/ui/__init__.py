"""Alfred's interface: optional, on demand, and on loopback only."""

from src.ui.live import BUS, LIVE, capture_output
from src.ui.server import INTERFACE, Interface

__all__ = ["BUS", "LIVE", "INTERFACE", "Interface", "capture_output"]
