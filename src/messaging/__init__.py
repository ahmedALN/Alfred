"""Talking to Alfred from somewhere other than the room it is in.

The hard parts of a message channel - proving the message really came
from the person it claims to, deciding what a stranger is allowed to do,
turning a line of text into work and the work back into a reply - have
nothing to do with which messaging service carries it. They live here;
the service-specific part is a small adapter.

That matters more than tidiness. This channel reaches an assistant that
can run anything on the machine, so whoever can post into it owns the
machine. Everything here is written on that basis.
"""

from src.messaging.base import Channel, Inbound
from src.messaging.router import MessageRouter

__all__ = ["Channel", "Inbound", "MessageRouter"]
