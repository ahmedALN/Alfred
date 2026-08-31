"""Talking to yourself on WhatsApp, without talking to yourself forever."""

from src.messaging.whatsapp_personal import PersonalWhatsApp, _text_of


class _Client:
    def __init__(self):
        self.sent = []

    def send_message(self, jid, text, *a, **k):
        self.sent.append((getattr(jid, "User", str(jid)), text))


def _channel(owner="+44 7700 900123"):
    channel = PersonalWhatsApp("session", owner)
    channel._client = _Client()
    return channel


def _event(text, sender="447700900123"):
    class Source:
        Sender = type("J", (), {"User": sender})()

    class Info:
        MessageSource = Source()

    class Message:
        conversation = text

    return type("Ev", (), {"Message": Message(), "Info": Info()})()


# ------------------------------------------------------------ reading


def test_a_plain_message_is_read():
    assert _text_of(_event("open notepad")) == "open notepad"


def test_a_quoted_reply_is_read_too():
    class Extended:
        text = "and then close it"

    class Message:
        conversation = ""
        extendedTextMessage = Extended()

    assert _text_of(type("Ev", (), {"Message": Message()})()) == \
        "and then close it"


def test_something_with_no_words_reads_as_nothing():
    assert _text_of(type("Ev", (), {"Message": None})()) == ""


# ------------------------------------------------ not answering itself


def test_alfred_does_not_reply_to_its_own_message():
    """In your own chat every message is "from me", including the ones
    Alfred just sent. Answering those is an endless conversation with
    itself."""
    channel = _channel()
    heard = []
    channel._on_message = heard.append

    channel.send("On it.")
    channel._deliver(_event("On it."))

    assert heard == []


def test_it_still_answers_the_same_words_said_again():
    """Suppressing one echo must not deafen it to a real message that
    happens to match."""
    channel = _channel()
    heard = []
    channel._on_message = heard.append

    channel.send("status")
    channel._deliver(_event("status"))     # the echo
    channel._deliver(_event("status"))     # the user, actually asking

    assert len(heard) == 1


def test_an_ordinary_message_gets_through():
    channel = _channel()
    heard = []
    channel._on_message = heard.append

    channel._deliver(_event("open steam"))

    assert [m.text for m in heard] == ["open steam"]
    assert heard[0].sender == "447700900123"


def test_a_failed_send_is_not_remembered_as_an_echo():
    """Otherwise the next real message matching it would be swallowed."""
    class Broken(_Client):
        def send_message(self, *a, **k):
            raise RuntimeError("not connected")

    channel = _channel()
    channel._client = Broken()
    heard = []
    channel._on_message = heard.append

    assert channel.send("hello") is False
    channel._deliver(_event("hello"))

    assert [m.text for m in heard] == ["hello"]


# ------------------------------------------------------------ sending


def test_messages_go_to_your_own_chat_by_default():
    channel = _channel()

    channel.send("Finished the research.")

    assert channel._client.sent == [("447700900123", "Finished the research.")]


def test_a_number_written_any_way_reaches_the_same_chat():
    channel = _channel(owner="07700 900123")

    channel.send("hello")

    assert channel._client.sent[0][0] == "07700900123"


def test_an_empty_message_is_not_sent():
    channel = _channel()

    assert channel.send("   ") is False
    assert channel._client.sent == []
