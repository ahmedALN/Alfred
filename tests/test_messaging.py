"""A message channel that can run anything on the machine.

Everything here is written on the basis that whoever can post into this
owns the PC, so the tests are mostly about who is turned away.
"""

import hashlib
import hmac
import json

from src.messaging.base import Inbound
from src.messaging.router import MessageRouter, _normalise
from src.messaging.whatsapp import WhatsAppChannel

MINE = "+44 7700 900123"
THEIRS = "+1 999 888 7777"


class _Channel:
    name = "test"

    def __init__(self):
        self.sent = []

    def send(self, text, to=None):
        self.sent.append((to, text))
        return True


def _router(allowed=(MINE,), status=None):
    channel = _Channel()
    jobs = []
    router = MessageRouter(
        channel, list(allowed), lambda text: jobs.append(text) or "id",
        status=status,
    )
    return router, channel, jobs


# ------------------------------------------------------------- who may talk


def test_the_owner_is_obeyed():
    router, channel, jobs = _router()

    router.handle(Inbound(MINE, "open notepad"))

    assert jobs == ["open notepad"]
    assert channel.sent and "On it" in channel.sent[0][1]


def test_anybody_else_is_ignored_entirely():
    """Not answered, not argued with - a reply confirms the number is
    live and reaches something worth attacking."""
    router, channel, jobs = _router()

    router.handle(Inbound(THEIRS, "delete everything"))

    assert jobs == [] and channel.sent == []
    assert router.refused == 1


def test_an_empty_allowlist_turns_everyone_away():
    """Nobody having been named is not permission for anyone."""
    router, channel, jobs = _router(allowed=())

    router.handle(Inbound(MINE, "open notepad"))

    assert jobs == [] and channel.sent == []


def test_the_same_number_written_differently_is_the_same_person():
    router, _, jobs = _router()

    for form in ("447700900123", "07700900123", "+44 7700 900123"):
        router.handle(Inbound(form, "hello"))

    assert len(jobs) == 3


def test_two_different_numbers_are_two_people():
    assert _normalise("447700900123") != _normalise("447700900124")


# ------------------------------------------------------------- what is said


def test_a_message_carrying_a_secret_is_refused_and_not_run():
    """Alfred cannot use a password and should not have one sitting in a
    chat log."""
    router, channel, jobs = _router()

    router.handle(Inbound(MINE, "my password is hunter2, log me in"))

    assert jobs == []
    assert "delete that message" in channel.sent[0][1]


def test_status_is_answered_without_starting_a_job():
    router, channel, jobs = _router(status=lambda: "Nothing running.")

    router.handle(Inbound(MINE, "status"))

    assert jobs == []
    assert channel.sent[0][1] == "Nothing running."


def test_an_empty_message_does_nothing():
    router, channel, jobs = _router()

    router.handle(Inbound(MINE, "   "))

    assert jobs == [] and channel.sent == []


def test_a_failure_to_start_is_reported_rather_than_swallowed():
    channel = _Channel()

    def broken(_text):
        raise RuntimeError("the queue is down")

    router = MessageRouter(channel, [MINE], broken)
    router.handle(Inbound(MINE, "do something"))

    assert "couldn't start that" in channel.sent[0][1]


def test_notify_reaches_the_owner_and_nobody_else():
    router, channel, _ = _router()

    router.notify("Finished the research.")

    assert len(channel.sent) == 1
    assert channel.sent[0][1] == "Finished the research."


# ---------------------------------------------------------------- whatsapp


def _signed(secret: bytes, body: bytes) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def _payload(text="hello", sender="447700900123", kind="text"):
    message = {"type": kind, "from": sender}
    if kind == "text":
        message["text"] = {"body": text}
    return json.dumps(
        {"entry": [{"changes": [{"value": {"messages": [message]}}]}]}
    ).encode()


def test_a_forged_payload_is_refused():
    """The signature is what separates Meta from anyone who found the
    address."""
    channel = WhatsAppChannel("t", "1", app_secret="s3cret")
    got = []
    channel.start(got.append)

    assert channel.deliver(_payload(), "sha256=deadbeef") == 0
    assert got == []


def test_a_signed_payload_is_delivered():
    channel = WhatsAppChannel("t", "1", app_secret="s3cret")
    got = []
    channel.start(got.append)
    body = _payload("open notepad")

    assert channel.deliver(body, _signed(b"s3cret", body)) == 1
    assert got[0].text == "open notepad"


def test_without_an_app_secret_nothing_is_trusted():
    """A webhook that cannot tell Meta from a stranger must not be
    trusted with a machine."""
    channel = WhatsAppChannel("t", "1", app_secret="")
    got = []
    channel.start(got.append)
    body = _payload()

    assert channel.deliver(body, _signed(b"anything", body)) == 0
    assert got == []


def test_only_text_messages_are_acted_on():
    """An image or a voice note is not something to act on blindly."""
    channel = WhatsAppChannel("t", "1", app_secret="s")

    assert channel.parse(_payload(kind="image")) == []
    assert len(channel.parse(_payload(kind="text"))) == 1


def test_the_subscription_check_needs_the_right_token():
    channel = WhatsAppChannel("t", "1", verify_token="vt")

    assert channel.verify_subscription("subscribe", "vt", "CHAL") == "CHAL"
    assert channel.verify_subscription("subscribe", "wrong", "CHAL") is None
    assert channel.verify_subscription("unsubscribe", "vt", "CHAL") is None


def test_rubbish_in_the_payload_does_not_raise():
    channel = WhatsAppChannel("t", "1", app_secret="s")

    assert channel.parse(b"not json at all") == []
    assert channel.parse(b"{}") == []


def test_messages_go_out_to_the_number_as_it_was_given():
    """The matching form is the last ten digits, which has no country
    code and would not reach anybody if it were dialled."""
    router, channel, _ = _router()

    router.notify("done")

    assert channel.sent[0][0] == MINE


def test_a_reply_goes_back_to_whoever_wrote():
    router, channel, _ = _router()

    router.handle(Inbound("447700900123", "hello"))

    assert channel.sent[0][0] == "447700900123"


def test_the_wire_format_is_digits_only():
    from src.messaging.whatsapp import _wire_number

    assert _wire_number("+44 7700 900123") == "447700900123"
