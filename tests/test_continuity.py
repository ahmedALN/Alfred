"""One conversation, not eleven.

The voice session is torn down and rebuilt every hundred and fifty
seconds by the model provider, and each one was given a new id. So a
day of talking was eleven separate conversations, none of which could
see the others, and "about that thing earlier" had nothing to refer to.

The phone was a twelfth, in a different place entirely.
"""

from src.context import build_situation
from src.memory.store import MemoryStore
from src.messaging.reply import Conversation


def _store(tmp_path):
    return MemoryStore(tmp_path / "m.sqlite3")


# ------------------------------------------------ across the sessions


def test_what_was_said_in_another_session_is_still_ours(tmp_path):
    """Continuity is not a session property. It belongs to the person."""
    store = _store(tmp_path)
    store.add_turn("session-one", "user", "open steam")
    store.add_turn("session-one", "alfred", "Steam is open.")
    store.add_turn("session-two", "user", "and hollow knight")

    said = store.recent_turns(limit=8)

    assert [t["text"] for t in said] == [
        "open steam", "Steam is open.", "and hollow knight",
    ]


def test_the_newest_come_back_in_the_order_they_were_said(tmp_path):
    store = _store(tmp_path)
    for n in range(10):
        store.add_turn("s", "user", f"thing {n}")

    said = store.recent_turns(limit=3)

    assert [t["text"] for t in said] == ["thing 7", "thing 8", "thing 9"]


def test_it_reaches_every_prompt(tmp_path):
    store = _store(tmp_path)
    store.add_turn("s", "user", "what about the physics coursework")

    situation = build_situation(memory=store)

    assert "Just before this" in situation
    assert "physics coursework" in situation


def test_nothing_said_puts_no_empty_heading_in_the_prompt(tmp_path):
    assert "Just before this" not in build_situation(memory=_store(tmp_path))


def test_a_broken_memory_does_not_take_the_situation_down(tmp_path):
    class _Broken:
        def recent_turns(self, **kw):
            raise RuntimeError("locked")

    assert build_situation(memory=_Broken())      # still produces something


# --------------------------------------------- both doors, one thread


def test_the_phone_writes_into_the_same_thread(tmp_path):
    """So what you said on WhatsApp is known in the room."""
    store = _store(tmp_path)

    class _Chat:
        def generate(self, prompt, **kw):
            return "SAY: Evening."

    talk = Conversation(
        _Chat(), lambda job: None,
        record=lambda role, text: store.add_turn("phone", role, text),
    )
    talk.handle("you there?")

    said = [t["text"] for t in store.recent_turns()]
    assert "you there?" in said
    assert "Evening." in said


def test_a_picture_is_remembered_as_one(tmp_path):
    store = _store(tmp_path)

    class _Eyes:
        def analyze(self, data, prompt, *, mime_type="image/png"):
            return "A Python traceback."

    talk = Conversation(
        None, lambda job: None, eyes=_Eyes(),
        record=lambda role, text: store.add_turn("phone", role, text),
    )
    talk.handle("what is this", media=b"JPEG", kind="image")

    assert any("[a picture]" in t["text"] for t in store.recent_turns())


def test_a_channel_with_nowhere_to_write_still_works():
    class _Chat:
        def generate(self, prompt, **kw):
            return "SAY: Evening."

    assert Conversation(_Chat(), lambda job: None).handle("hi") == "Evening."
