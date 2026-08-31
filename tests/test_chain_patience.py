"""Not being stuck with a choice made before the call started.

The bench watched a four-second question take a hundred and twenty-three
seconds, one tool call, no failures - the good model was simply having a
bad minute, and the transport timeout underneath is three minutes. That
is the right patience for a request nobody is waiting on and quite the
wrong one for something somebody asked out loud.
"""

import time

from src.ai.providers.fallback import FallbackChatProvider


class _Provider:
    def __init__(self, name, answer="ok", delay=0.0, fail=False):
        self.name = name
        self.model = name
        self.answer = answer
        self.delay = delay
        self.fail = fail
        self.calls = 0

    def generate(self, prompt, **kw):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError(self.name + " is down")
        return self.answer

    def unload(self):
        pass


def _chain(*providers, patience=0.15):
    return FallbackChatProvider(list(providers), patience=patience)


def test_a_healthy_primary_is_still_used():
    fast = _Provider("fast", "from the good one")
    spare = _Provider("spare", "from the spare")

    assert _chain(fast, spare).generate("hi") == "from the good one"
    assert spare.calls == 0


def test_a_primary_having_a_bad_minute_is_moved_past():
    slow = _Provider("slow", delay=2.0)
    spare = _Provider("spare", "from the spare")

    started = time.time()
    answer = _chain(slow, spare).generate("hi")

    assert answer == "from the spare"
    assert time.time() - started < 1.5      # did not sit through the 2s


def test_the_slow_one_is_not_asked_again_straight_away():
    """Waiting for it once is a cost worth paying. Twice is not."""
    slow = _Provider("slow", delay=2.0)
    spare = _Provider("spare", "from the spare")
    chain = _chain(slow, spare)

    chain.generate("one")
    chain.generate("two")

    assert slow.calls == 1


def test_the_last_rung_is_never_hurried():
    """It is the safety net. Hurrying it along leaves nowhere to fall."""
    down = _Provider("down", fail=True)
    slow_but_final = _Provider("local", "from the local one", delay=0.4)

    assert _chain(down, slow_but_final).generate("hi") == "from the local one"


def test_everything_being_slow_still_produces_an_answer():
    slow = _Provider("slow", delay=1.0)
    also_slow = _Provider("also", "eventually", delay=0.3)

    assert _chain(slow, also_slow).generate("hi") == "eventually"


# ------------------------------------------------------- the fast lane


def test_small_jobs_get_their_own_model():
    """Reading an answer out of one line of PowerShell output does not
    need the planner, and going through it made a learned routine - one
    tool call, no thinking - take eleven seconds."""
    from src.brain.agent import TaskAgent
    from src.brain.policy import Policy

    planner = _Provider("planner")
    fast = _Provider("fast")

    agent = TaskAgent(
        planner, _Registry(), Policy("full", {"x"}, surface="brain"),
        fast_chat=fast,
    )

    assert agent._fast_chat is fast
    assert agent._plan_chat is planner


def test_without_one_it_carries_on_as_before():
    from src.brain.agent import TaskAgent
    from src.brain.policy import Policy

    planner = _Provider("planner")
    agent = TaskAgent(
        planner, _Registry(), Policy("full", {"x"}, surface="brain")
    )

    assert agent._fast_chat is planner


class _Registry:
    def gemini_declarations(self):
        return [{"name": "x", "description": "d"}]

    def execute(self, name, args):
        return {"status": "success"}
