"""Learning a way round a wall, rather than a lesson from one bad run."""

from src.brain.agent import Step, TaskAgent
from src.brain.limitations import LimitationStore, shape_of


def _store(tmp_path):
    return LimitationStore(tmp_path / "limits.sqlite3")


# ---------------------------------------------------------------- shapes


def test_the_same_wall_is_recognised_whatever_it_was_reached_for():
    """"no control matches name='Deji'" and "...name='Launch'" are the
    same wall; counting them apart means never noticing it."""
    a = shape_of("ui_control", "no control matches ref=None name='Deji'", "yt")
    b = shape_of("ui_control", "no control matches ref=None name='Launch'", "yt")

    assert a == b


def test_different_walls_stay_different():
    assert shape_of("ui_control", "window not found: X") != shape_of(
        "ui_control", "no control matches name=Y")
    assert shape_of("ui_control", "same text") != shape_of(
        "powershell", "same text")


def test_the_same_error_in_two_apps_is_two_walls():
    """Steam refusing to expose its tree is a different problem from
    MultiMC doing it, and they have different answers."""
    assert shape_of("ui_control", "empty tree", "steam") != shape_of(
        "ui_control", "empty tree", "multimc")


# ------------------------------------------------------------- counting


def test_one_bad_run_teaches_nothing(tmp_path):
    """A lesson written from bad luck is a fact that is not true."""
    store = _store(tmp_path)
    signature = store.hit("ui_control", "no search box found", "steam")
    store.got_past(signature, "desktop_control look")

    assert store.ready_to_teach() == []
    store.close()


def test_a_wall_hit_twice_with_a_way_past_is_worth_teaching(tmp_path):
    store = _store(tmp_path)
    signature = store.hit("ui_control", "no search box found", "steam")
    store.hit("ui_control", "no search box found", "steam")
    store.got_past(signature, "desktop_control look")

    ready = store.ready_to_teach()
    assert len(ready) == 1
    assert ready[0]["hits"] == 2
    assert ready[0]["workaround"] == "desktop_control look"
    store.close()


def test_a_repeated_wall_with_no_way_past_is_not_taught_but_is_kept(tmp_path):
    """Nothing useful to say except "this fails" - but it is worth
    knowing that it keeps failing."""
    store = _store(tmp_path)
    for _ in range(4):
        store.hit("ui_control", "roblox exposes nothing", "roblox")

    assert store.ready_to_teach() == []
    assert len(store.unsolved(min_hits=3)) == 1
    store.close()


def test_a_lesson_is_only_taught_once(tmp_path):
    store = _store(tmp_path)
    signature = store.hit("ui_control", "no box", "steam")
    store.hit("ui_control", "no box", "steam")
    store.got_past(signature, "clear_popups then search")

    assert len(store.ready_to_teach()) == 1
    store.mark_taught(signature)
    assert store.ready_to_teach() == []
    store.close()


# --------------------------------------------------------- through the agent


class _Learner:
    def __init__(self):
        self.facts = []

    def remember(self, content, category="", source=""):
        self.facts.append((content, source))


def _agent(store, learner=None):
    agent = object.__new__(TaskAgent)
    agent._limitations = store
    agent._learner = learner
    agent._app_memory = None
    agent._last_wall = ""
    return agent


def _step(tool, ok, error="", args=None):
    return Step(1, "", tool, args or {}, "auto",
                {"status": "error", "error": error} if error else {}, ok)


def test_the_same_tool_working_afterwards_is_the_way_past(tmp_path):
    """The only evidence of a workaround worth having: a route that
    actually worked on the thing that failed."""
    store = _store(tmp_path)
    agent = _agent(store)

    agent._note_wall(_step("ui_control", False, "no search box found",
                           {"window": "Steam"}))
    agent._note_wall(_step("ui_control", True,
                           args={"action": "search", "window": "Steam"}))

    rows = store.all()
    assert "search" in rows[0]["workaround"]
    store.close()


def test_whatever_happened_next_is_not_a_workaround(tmp_path):
    """Alfred failed a PowerShell command, went and looked at the
    screen, and banked "when powershell fails, use desktop_control
    look" as a standing lesson. That is not a route round anything - it
    is just the next thing that happened. A wrong lesson is worse than
    no lesson, because it gets followed."""
    store = _store(tmp_path)
    agent = _agent(store)

    agent._note_wall(_step("powershell", False, "Cannot convert Downloads"))
    agent._note_wall(_step("desktop_control", True, args={"action": "look"}))

    assert store.all()[0]["workaround"] == ""
    store.close()


def test_a_success_with_no_wall_before_it_records_nothing(tmp_path):
    store = _store(tmp_path)
    agent = _agent(store)

    agent._note_wall(_step("ui_control", True, args={"action": "click"}))

    assert store.all() == []
    store.close()


def test_the_agent_banks_the_lesson_once_it_has_earned_it(tmp_path):
    store, learner = _store(tmp_path), _Learner()
    agent = _agent(store, learner)

    for _ in range(2):
        agent._note_wall(_step("ui_control", False, "no search box found",
                               {"window": "Steam"}))
        agent._note_wall(_step("ui_control", True,
                               args={"action": "search", "window": "Steam"}))

    learned = agent.learn_workarounds()

    assert len(learned) == 1
    assert "search" in learned[0]
    assert "Steam" in learned[0]
    assert learner.facts[0][1] == "learned_workaround"
    # And never twice.
    assert agent.learn_workarounds() == []
    store.close()


def test_nothing_is_banked_from_a_single_encounter(tmp_path):
    store, learner = _store(tmp_path), _Learner()
    agent = _agent(store, learner)

    agent._note_wall(_step("ui_control", False, "no box", {"window": "Steam"}))
    agent._note_wall(_step("ui_control", True, args={"action": "search"}))

    assert agent.learn_workarounds() == []
    assert learner.facts == []
    store.close()


def test_recording_survives_a_broken_store(tmp_path):
    class Broken:
        def hit(self, *a, **k):
            raise RuntimeError("disk is gone")

    agent = _agent(Broken())
    agent._note_wall(_step("ui_control", False, "anything"))   # must not raise
