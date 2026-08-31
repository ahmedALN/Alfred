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


def _event(text, sender="271712549638356", chat=None, from_me=True):
    """Shaped like a real one: WhatsApp addresses by LID, not by number."""

    class Source:
        Sender = type("J", (), {"User": sender})()
        Chat = type("J", (), {"User": sender if chat is None else chat})()
        IsFromMe = from_me

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


class _Holding(_SlowClient):
    """A real connection lasts the whole session; it is not a call."""

    def __init__(self, refusals=0):
        super().__init__(refusals=refusals)
        self.let_go = threading.Event()

    def connect(self):
        self.connected_calls += 1
        self.let_go.wait(5)

    def stop(self):
        self.let_go.set()


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
    client = _Holding(refusals=0)
    channel = _paired(client)
    channel.pair()

    assert channel._thread is not None
    channel.stop()
    assert client.connected_calls == 1


def test_it_only_connects_once_however_many_times_it_is_asked():
    """Pairing and then listening is one connection, not three."""

    client = _Holding(refusals=0)
    channel = _paired(client)

    channel.pair()
    channel.start(lambda _m: None)
    channel.pair()
    channel.stop()

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


# ------------------------------------------------------ letting go


def test_stopping_cancels_the_worker_rather_than_just_closing_the_socket():
    """The library's own thread is not a daemon: disconnect() alone
    leaves the program alive after main() returns."""
    called = []

    class _Client(_SlowClient):
        def stop(self):
            called.append("stop")
            self.let_go.set()

        def disconnect(self):
            called.append("disconnect")

        def connect(self):
            self.connected_calls += 1
            self.let_go.wait(5)

    client = _Client(refusals=0)
    client.let_go = threading.Event()
    channel = _paired(client)
    channel.start(lambda _m: None)
    channel.stop()

    assert called == ["stop"]
    assert channel.connected is False


def test_it_still_lets_go_of_a_client_that_has_no_stop():
    called = []

    class _Old(_SlowClient):
        def disconnect(self):
            called.append("disconnect")

    channel = _paired(_Old(refusals=0))
    channel.stop()

    assert called == ["disconnect"]


def test_stopping_waits_for_the_thread_to_actually_finish():
    class _Client(_SlowClient):
        def __init__(self):
            super().__init__(refusals=0)
            self.let_go = threading.Event()

        def stop(self):
            self.let_go.set()

        def connect(self):
            self.let_go.wait(5)

    client = _Client()
    channel = _paired(client)
    channel.start(lambda _m: None)
    thread = channel._thread
    channel.stop()

    assert not thread.is_alive()


# ------------------------------------------- knowing when it goes deaf


def test_it_listens_for_being_unseated_not_just_for_messages(tmp_path):
    """WhatsApp allows one live connection per linked device. A second
    one unseats the first silently, and a silently deaf Alfred looks
    exactly like an Alfred ignoring you."""
    from neonize.events import (
        ConnectedEv,
        DisconnectedEv,
        EVENT_TO_INT,
        LoggedOutEv,
        MessageEv,
        StreamReplacedEv,
    )

    channel = PersonalWhatsApp(tmp_path / "s.sqlite3", "+447700900123")
    client = channel._build()
    subscribed = set(client.event.list_func)

    for event in (ConnectedEv, MessageEv, StreamReplacedEv,
                  LoggedOutEv, DisconnectedEv):
        assert EVENT_TO_INT[event] in subscribed, event.__name__


def test_being_unseated_stops_it_calling_itself_connected(tmp_path):
    from neonize.events import EVENT_TO_INT, StreamReplacedEv

    channel = PersonalWhatsApp(tmp_path / "s.sqlite3", "+447700900123")
    client = channel._build()
    channel.connected = True

    client.event.list_func[EVENT_TO_INT[StreamReplacedEv]](client, None)

    assert channel.connected is False
    assert channel.displaced is True


# --------------------------------------------- knowing which chat is mine


def test_a_message_in_my_own_chat_gets_through():
    """The one that did not: WhatsApp addressed it 271712549638356,
    which is a LID and nothing like the number it came from."""
    channel = _channel()
    heard = []
    channel._on_message = heard.append

    channel._deliver(_event("Alfred are you there?"))

    assert [m.text for m in heard] == ["Alfred are you there?"]


def test_it_is_reported_as_coming_from_me_not_from_a_lid():
    """Everything downstream - the allowlist, the reply - is written in
    terms of the number, and reaching that point proves whose account
    it was."""
    channel = _channel(owner="+447435589157")
    heard = []
    channel._on_message = heard.append

    channel._deliver(_event("Hello"))

    assert heard[0].sender == "447435589157"


def test_what_i_say_to_other_people_is_not_an_instruction_to_alfred():
    """A linked device sees every chat on the account. Only one of them
    is talking to Alfred."""
    channel = _channel()
    heard = []
    channel._on_message = heard.append

    channel._deliver(_event("see you at eight", chat="447700900999"))

    assert heard == []


def test_what_other_people_say_to_me_is_not_an_instruction_either():
    channel = _channel()
    heard = []
    channel._on_message = heard.append

    channel._deliver(
        _event("delete everything", sender="447700900999", from_me=False)
    )

    assert heard == []


def test_a_stranger_cannot_get_in_by_messaging_from_their_own_chat():
    """Sender equal to chat is true of any one-to-one conversation. It
    is "from me" that says whose account produced it, and that cannot be
    forged by someone else's."""
    channel = _channel()
    heard = []
    channel._on_message = heard.append

    channel._deliver(
        _event("open my bank", sender="447700900999", from_me=False)
    )

    assert heard == []


def test_a_group_i_post_in_is_not_my_own_chat():
    channel = _channel()
    heard = []
    channel._on_message = heard.append

    channel._deliver(_event("anyone free?", chat="120363000000000000"))

    assert heard == []


# ----------------------------------------------------- coming back


def test_a_dropped_line_is_redialled():
    """Sleeping laptops and dead wifi should not leave Alfred deaf
    until somebody happens to notice."""
    tries = []

    class _Drops(_SlowClient):
        def connect(self):
            self.connected_calls += 1
            tries.append(1)
            if len(tries) >= 3:
                channel._stopping = True   # enough to prove the point

    channel = _paired(_Drops(refusals=0))
    channel.retry_wait = 0.01
    channel.start(lambda _m: None)
    channel._thread.join(3)

    assert len(tries) == 3


def test_a_line_someone_else_took_is_left_alone():
    """Reconnecting there is two Alfreds unseating each other for
    ever."""

    class _Displaced(_SlowClient):
        def connect(self):
            self.connected_calls += 1
            channel.displaced = True

    channel = _paired(_Displaced(refusals=0))
    channel.retry_wait = 0.01
    channel.start(lambda _m: None)
    channel._thread.join(3)

    assert channel._client.connected_calls == 1
