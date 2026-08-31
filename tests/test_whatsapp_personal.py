"""Talking to yourself on WhatsApp, without talking to yourself forever."""

import threading

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


# --------------------------------------------- waiting for the line up


class _SlowClient(_Client):
    """WhatsApp is not up the instant you ask for it."""

    def __init__(self, refusals=3):
        super().__init__()
        self.refusals = refusals
        self.asked = 0
        self.connected_calls = 0

    def qr(self, _fn):
        pass

    def connect(self):
        self.connected_calls += 1

    def PairPhone(self, phone, notify, *a, **k):
        self.asked += 1
        if self.asked <= self.refusals:
            raise RuntimeError("client is nil")
        return "12345678"


def _paired(client, owner="+447700900123"):
    channel = PersonalWhatsApp("session", owner)
    channel._client = client
    return channel


def test_it_waits_for_whatsapp_to_come_up_before_taking_no_for_an_answer():
    """"client is nil" means asked too early, not refused."""
    client = _SlowClient(refusals=3)
    channel = _paired(client)

    assert channel.pair() == "12345678"
    assert client.asked == 4


def test_it_starts_connecting_because_a_code_needs_a_live_connection():
    client = _SlowClient(refusals=0)
    channel = _paired(client)
    channel.pair()

    assert channel._thread is not None
    channel._thread.join(2)
    assert client.connected_calls == 1


def test_it_only_connects_once_however_many_times_it_is_asked():
    """Pairing and then listening is one connection, not three."""

    class _Holds(_SlowClient):
        def __init__(self):
            super().__init__(refusals=0)
            self.let_go = threading.Event()

        def connect(self):
            self.connected_calls += 1
            self.let_go.wait(5)          # a real one lasts the session

    client = _Holds()
    channel = _paired(client)

    channel.pair()
    channel.start(lambda _m: None)
    channel.pair()
    client.let_go.set()
    channel._thread.join(2)

    assert client.connected_calls == 1


def test_a_real_refusal_is_not_retried():
    class _Refuses(_SlowClient):
        def PairPhone(self, phone, notify, *a, **k):
            self.asked += 1
            raise RuntimeError("phone number is not registered")

    client = _Refuses()
    channel = _paired(client)

    try:
        channel.pair()
    except RuntimeError as exc:
        assert "not registered" in str(exc)
    else:
        raise AssertionError("should have given up")

    assert client.asked == 1


def test_it_gives_up_eventually_rather_than_asking_for_ever():
    client = _SlowClient(refusals=10_000)
    channel = _paired(client)

    try:
        channel.pair(timeout=0.5)
    except RuntimeError as exc:
        assert "client is nil" in str(exc)
    else:
        raise AssertionError("should have given up")


def test_the_number_it_pairs_with_has_no_punctuation_in_it():
    seen = {}

    class _Records(_SlowClient):
        def PairPhone(self, phone, notify, *a, **k):
            seen["phone"] = phone
            return "87654321"

    channel = _paired(_Records(), owner="+44 (7700) 900-123")
    channel.pair()

    assert seen["phone"] == "447700900123"
