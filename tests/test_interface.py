"""The window: what it reads, what it may change, and who may ask."""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.request

import pytest

from src.ui import edits, state
from src.ui.live import BUS, Bus, Tee
from src.ui.server import Interface

# ------------------------------------------------------------------ state


def test_a_missing_store_is_an_empty_panel_not_a_crash(tmp_path, monkeypatch):
    """The interface must open when Alfred has never run.

    Reading its files rather than reaching into the process is the
    whole point: you want to look at what it believes most urgently
    just after it has fallen over.
    """
    monkeypatch.setattr(state, "_ROOT", tmp_path)
    assert state.memory() == []
    assert state.skills() == []
    assert state.tasks() == []
    assert state.life()["all"] == []
    assert state.overview()["facts"] == 0
    # And the whole picture still assembles.
    assert set(state.everything()) >= {"overview", "memory", "life"}


def test_a_corrupt_store_is_also_survivable(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "_ROOT", tmp_path)
    (tmp_path / "alfred_memory.sqlite3").write_text("not a database")
    assert state.memory() == []


# ------------------------------------------------------------------ edits


@pytest.fixture()
def facts(tmp_path, monkeypatch):
    path = tmp_path / "alfred_memory.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE facts (id INTEGER PRIMARY KEY, content TEXT, "
        "category TEXT, confidence REAL, times_reinforced INTEGER, "
        "source TEXT, embedding TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO facts (id, content, category, confidence, "
        "times_reinforced, source, embedding, created_at, updated_at) "
        "VALUES (1, 'the user hates tea', 'you', 1.0, 1, 'learned', "
        "'[0.1]', '2026-01-01', '2026-01-01')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(edits, "_ROOT", tmp_path)
    monkeypatch.setattr(state, "_ROOT", tmp_path)
    return path


def test_a_wrong_belief_can_be_deleted(facts):
    assert edits.forget_fact(1) is True
    assert state.memory() == []


def test_correcting_a_fact_drops_the_embedding_it_no_longer_matches(facts):
    edits.correct_fact(1, "the user likes tea")
    row = sqlite3.connect(facts).execute(
        "SELECT content, embedding, source FROM facts WHERE id = 1"
    ).fetchone()
    assert row[0] == "the user likes tea"
    # A vector describing the old wording would keep recalling this
    # fact for the old question.
    assert row[1] is None
    assert "corrected" in row[2]


def test_a_fact_cannot_be_emptied_by_accident(facts):
    with pytest.raises(edits.EditError):
        edits.correct_fact(1, "   ")


def test_editing_something_that_is_gone_says_so_in_words(facts):
    with pytest.raises(edits.EditError) as caught:
        edits.forget_fact(9999)
    assert "already be gone" in str(caught.value)


def test_the_browser_cannot_name_its_own_action(facts):
    with pytest.raises(edits.EditError):
        edits.apply("DROP TABLE facts", {})
    with pytest.raises(edits.EditError):
        edits.apply("delete_everything", {})


def test_an_action_missing_its_argument_says_which(facts):
    with pytest.raises(edits.EditError) as caught:
        edits.apply("forget_fact", {})
    assert "id" in str(caught.value)


# -------------------------------------------------------------------- bus


def test_a_window_that_stops_reading_cannot_grow_the_process():
    bus = Bus(keep=5)
    for i in range(50):
        bus.publish("log", line=str(i))
    # History is capped, and a subscriber that never drains is bounded
    # by its own queue rather than by anything Alfred does.
    assert len(bus.history(999)) == 5


def test_printing_reaches_the_window_a_line_at_a_time():
    bus = Bus()

    class Sink:
        def __init__(self): self.text = ""
        def write(self, t): self.text += t; return len(t)
        def flush(self): pass

    sink = Sink()
    tee = Tee(sink, bus)
    tee.write("[Brain] half a ")
    assert bus.history() == []          # nothing until the line ends
    tee.write("line\nand another\n")

    lines = [e["line"] for e in bus.history()]
    assert lines == ["[Brain] half a line", "and another"]
    # And the terminal still got every character.
    assert sink.text == "[Brain] half a line\nand another\n"


# ----------------------------------------------------------------- server


@pytest.fixture(scope="module")
def server():
    iface = Interface(port=8971)
    iface.token = "test-token"
    iface.start()
    for _ in range(60):
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{iface.port}/api/all?k={iface.token}",
                timeout=1,
            )
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    yield iface
    iface.stop()


def _get(iface, path):
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{iface.port}{path}", timeout=5
        ) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_a_page_you_are_browsing_cannot_read_your_memories(server):
    """Loopback is not privacy.

    Any site open in any browser can make requests to 127.0.0.1, and
    this server hands out mail, memories and the screen. Without the
    token it hands out nothing.
    """
    assert _get(server, "/api/all")[0] == 403
    assert _get(server, "/api/all?k=guessed")[0] == 403
    assert _get(server, "/")[0] == 403


def test_with_the_key_it_answers(server):
    status, body = _get(server, f"/api/all?k={server.token}")
    assert status == 200
    assert "overview" in json.loads(body)


def test_the_static_folder_is_a_floor_not_a_door(server):
    status, _ = _get(server, f"/static/../../../.env?k={server.token}")
    assert status in (403, 404)


def test_the_websocket_is_reachable_and_still_asks_for_the_key(server):
    """This is the check that would have caught the annotation bug.

    Every handshake was refused 403 because FastAPI could not resolve
    "socket: WebSocket" to a type and read it as a query parameter
    instead. Nothing in the logs said so, and it looked exactly like a
    rejected token - so the token is what gets tested here, both ways.
    """
    import asyncio

    import websockets

    async def hello(key: str):
        url = f"ws://127.0.0.1:{server.port}/ws?k={key}"
        async with websockets.connect(url) as socket:
            return json.loads(await asyncio.wait_for(socket.recv(), timeout=5))

    greeting = asyncio.run(hello(server.token))
    assert greeting["kind"] == "hello"
    assert "abilities" in greeting

    with pytest.raises(Exception):
        asyncio.run(hello("wrong"))


def test_talking_to_an_alfred_that_is_not_running_says_so(server):
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.port}/api/say?k={server.token}",
        data=json.dumps({"text": "hello"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=5)
        raise AssertionError("should have refused")
    except urllib.error.HTTPError as exc:
        assert exc.code == 409
        assert "not running" in json.loads(exc.read())["error"]


# ----------------------------------------------------------------- opener


def test_asking_twice_shows_the_window_rather_than_drawing_a_second(monkeypatch):
    from src.ui import opener

    spawned = []

    class Fake:
        def poll(self): return None          # still alive
        def terminate(self): pass

    monkeypatch.setattr(opener.INTERFACE, "start", lambda: "http://x/?k=1")
    monkeypatch.setattr(
        opener.subprocess, "Popen",
        lambda *a, **k: (spawned.append(a), Fake())[1],
    )
    monkeypatch.setattr(opener, "_process", None)

    first = opener.open_interface()
    assert first["what"] == "opened"
    assert len(spawned) == 1

    shown = []
    monkeypatch.setattr(opener.BUS, "publish",
                        lambda kind, **kw: shown.append(kind))
    second = opener.open_interface()
    assert second["what"] == "shown"
    assert shown == ["show_window"]
    assert len(spawned) == 1      # no second window

    monkeypatch.setattr(opener, "_process", None)


# ------------------------------------------------------------------- tool


def test_the_tool_refuses_an_action_it_does_not_have():
    from src.tools.interface_tool import InterfaceTool

    out = InterfaceTool().execute({"action": "explode"})
    assert out["status"] == "error"
    assert "open" in out["error"]


def test_the_tool_reports_whether_it_is_open(monkeypatch):
    from src.tools.interface_tool import InterfaceTool
    from src.ui import opener

    monkeypatch.setattr(opener, "is_open", lambda: False)
    out = InterfaceTool().execute({"action": "status"})
    assert out["open"] is False
    assert "not open" in out["said"]


# ---------------------------------------------------------- killing things


class _Proc:
    def __init__(self, pid, argv):
        self.pid = pid
        self.info = {"pid": pid, "name": "x", "cmdline": argv}
        self.killed = False

    def kill(self):
        self.killed = True


def test_clearing_stale_windows_does_not_kill_the_terminal(monkeypatch):
    """The first version of this killed the shell it was started from.

    It joined each command line into one string and looked for "src.ui"
    and "http" anywhere in it - which matches any shell whose command
    text merely mentions them, including the one running the tests.
    Killing by substring is a guess, and this one calls kill().
    """
    import psutil

    from src.ui import opener

    shell = _Proc(1, ["C:/Program Files/Git/bin/bash.exe", "-c",
                      "cd /c/Alfred && python -m src.ui http://127.0.0.1:1/x"])
    editor = _Proc(2, ["python.exe", "-m", "src.ui"])          # standalone
    window = _Proc(3, ["C:/Alfred/.venv/Scripts/pythonw.exe", "-m",
                       "src.ui", "http://127.0.0.1:8756/?k=abc"])
    elsewhere = _Proc(4, ["python.exe", "-m", "src.ui",
                          "http://example.com/?k=abc"])

    monkeypatch.setattr(psutil, "process_iter",
                        lambda _fields: [shell, editor, window, elsewhere])
    monkeypatch.setattr(opener.sys, "platform", "win32")

    assert opener._clear_orphans() == 1

    assert window.killed is True        # the one it actually spawned
    assert shell.killed is False        # the terminal it was started from
    assert editor.killed is False       # a standalone window, serving itself
    assert elsewhere.killed is False    # not even pointed at this machine


# ------------------------------------------------------------- the wiring


def test_every_live_hook_has_something_that_calls_it():
    """The window was built reactive and wired to nothing.

    set_level, set_speaking and the three task hooks all existed, all
    published to the bus, and had exactly zero callers - so the reactor
    sat on "idle" for ever, sound never ducked under Alfred's voice,
    and the task panel never moved. Everything looked finished.

    This reads the source rather than the behaviour on purpose: the
    defect was an absence, and absences do not show up in a test of the
    thing that is present.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "src"
    source = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in root.rglob("*.py")
        if "ui" not in path.parts[len(root.parts) - 1:]
    )

    for hook in ("set_level", "set_speaking",
                 "task_started", "task_step", "task_ended"):
        assert f".{hook}(" in source, (
            f"LIVE.{hook} is published to the interface but nothing in "
            f"Alfred ever calls it, so that part of the window is dead"
        )


def test_speaking_is_only_announced_when_it_changes():
    """Called every audio chunk, so it must not publish every chunk."""
    from src.ui.live import Live

    live = Live()
    before = len(BUS.history(999))

    live.set_speaking(True)
    live.set_speaking(True)
    live.set_speaking(True)
    live.set_speaking(False)

    kinds = [e["kind"] for e in BUS.history(999)[before:]]
    assert kinds == ["speaking", "speaking"]


def test_a_task_that_dies_still_clears_the_running_state():
    """An exception used to leave the window saying "working" for ever."""
    from src.ui.live import Live

    live = Live()
    live.task_started("t1", "open notepad")
    assert live.current_task["goal"] == "open notepad"
    live.task_ended("t1", "error", "it blew up")
    assert live.current_task is None


def test_the_visualiser_is_never_fed_nonsense():
    from src.ui.live import Live

    live = Live()
    live.set_level(4.2)
    assert live.level == 1.0
    live.set_level(-3)
    assert live.level == 0.0


# ---------------------------------------------------------------- toggle


def test_one_key_puts_it_away_and_brings_it_back(monkeypatch):
    """Ctrl+Alt+I used to only ever open it.

    Pressing it while the window was already up did nothing you could
    see, because "show what is already shown" is not a change.
    """
    from src.ui import opener

    showing = {"now": True}
    monkeypatch.setattr(opener, "is_showing", lambda: showing["now"])
    monkeypatch.setattr(
        opener, "hide_interface",
        lambda: (showing.__setitem__("now", False),
                 {"status": "success", "what": "hidden"})[1],
    )
    monkeypatch.setattr(
        opener, "open_interface",
        lambda: (showing.__setitem__("now", True),
                 {"status": "success", "what": "shown"})[1],
    )

    assert opener.toggle_interface()["what"] == "hidden"
    assert showing["now"] is False
    assert opener.toggle_interface()["what"] == "shown"
    assert showing["now"] is True


def test_hiding_a_window_that_is_not_there_is_not_an_error(monkeypatch):
    from src.ui import opener

    monkeypatch.setattr(opener, "_find_window", lambda: None)
    out = opener.hide_interface()
    assert out["status"] == "success"
    assert out["what"] == "was not open"


# ------------------------------------------------------------- escaping


def test_model_written_content_is_never_dropped_into_the_page_raw():
    """The window renders text Alfred wrote, and has full API access.

    Memories, skill templates, task goals, limitation details and log
    lines are all written by a model or copied from the machine. Any
    one of them reaching innerHTML unescaped is stored XSS on a page
    that can read the user's mail and delete what Alfred believes.

    Verified live once with an <img onerror> payload - it rendered as
    text. This keeps it that way: the risky fields must never appear
    inside a template literal without esc() around them.
    """
    import pathlib
    import re

    source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src" / "ui" / "static" / "app.js"
    ).read_text(encoding="utf-8")

    risky = [
        "f.content", "s.template", "s.name", "t.goal", "t.summary",
        "l.detail", "l.signature", "m.name", "m.detail", "l.line",
        "a.goal", "a.said", "m.text", "w.title",
    ]

    def emits_the_value(expr: str) -> bool:
        """Would this interpolation put the field's own text on the page?

        Two shapes provably would not, and both are in use: a regex
        .test() choosing a CSS class, and a ternary between two string
        literals. Neither carries the field's characters through.
        """
        if "esc(" in expr:
            return False
        return not (".test(" in expr or ".includes(" in expr)

    unescaped = []
    for field in risky:
        for match in re.finditer(r"\$\{([^}]*" + re.escape(field) + r"[^}]*)\}",
                                 source):
            expr = match.group(1)
            # row() escapes whatever it is handed as an id, so building
            # one out of a field is safe - but only the id line itself.
            # Looking backwards a fixed number of characters caught the
            # PREVIOUS property (`id: `fact-${f.id}`,`) and skipped the
            # real mistake on the line after it.
            start = source.rfind("\n", 0, match.start()) + 1
            this_line = source[start:source.find("\n", match.start())]
            if this_line.lstrip().startswith("id:"):
                continue
            if emits_the_value(expr):
                line = source[:match.start()].count("\n") + 1
                unescaped.append(f"{field} at line {line}: ${{{expr[:60]}}}")

    assert not unescaped, (
        "content Alfred wrote is being put into the page unescaped:\n  "
        + "\n  ".join(unescaped)
    )
