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


def test_the_slow_one_keeps_being_offered_the_work():
    """Being out-raced is not a failure, and must not bench a model.

    This used to assert the opposite - one slow call and the rung sat
    out. Measured on the real chain, that was the expensive half of the
    problem: gemini-flash-lite answers a planner prompt in 1.4s at the
    median and took 31.8s once, cold. Benching it for that meant the
    whole of the next task ran nine seconds a call on the rung below,
    when the very next call to the fast one came back in 1.02s.

    It costs one extra request in flight and nothing in latency,
    because the spare is racing it either way.
    """
    slow = _Provider("slow", delay=2.0)
    spare = _Provider("spare", "from the spare")
    chain = _chain(slow, spare)

    chain.generate("one")
    chain.generate("two")

    assert slow.calls == 2
    assert spare.calls == 2


def test_a_rung_that_actually_failed_does_sit_out():
    """The distinction being drawn: slow is not the same as broken."""
    broken = _Provider("broken", fail=True)
    spare = _Provider("spare", "from the spare")
    chain = _chain(broken, spare)

    chain.generate("one")
    chain.generate("two")

    assert broken.calls == 1


def test_the_slow_one_still_wins_if_it_gets_there_first():
    """The whole point of racing: its work is not thrown away.

    The spare is started alongside after patience runs out, but the
    slow rung is earlier in the chain and finishes sooner, so its
    answer is the one used.
    """
    slow = _Provider("slow", "from the good one", delay=0.3)
    slower_spare = _Provider("spare", "from the spare", delay=1.0)

    assert _chain(slow, slower_spare, patience=0.05).generate("hi") == (
        "from the good one"
    )


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


# ------------------------------- resting a rung for as long as it deserves


from src.ai.providers.fallback import _rest_for


def _rest(message, default=600.0, patience=12.0):
    return _rest_for(Exception(message), default, patience)


def test_a_blip_is_seconds_not_minutes():
    """One 503 - over before you noticed - used to bench the best model
    for ten minutes, which is several tasks. That is most of why Alfred
    spent a night planning with a 4B local model."""
    assert _rest("503 Service Unavailable") <= 60
    assert _rest("connection reset by peer") <= 60
    assert _rest("no answer in 12s") <= 60


def test_running_out_of_allowance_is_waited_out():
    assert _rest("429 RESOURCE_EXHAUSTED: quota") == 600.0


def test_a_refused_key_is_not_retried_all_afternoon():
    assert _rest("401 unauthorized") >= 3600


def test_being_told_when_to_come_back_is_believed():
    assert _rest("Retry-After: 45") == 45.0


def test_a_ridiculous_retry_after_is_capped():
    assert _rest("retry-after: 99999") == 3600.0


def test_something_unrecognised_gets_the_ordinary_wait():
    assert _rest("something nobody has seen before") == 600.0


# ------------------------------------------------- knowing it is degraded


def test_it_knows_when_it_is_not_on_the_best_model():
    """Until now this happened silently, and the only clue was
    everything taking longer and going wrong more."""
    down = _Provider("down", fail=True)
    spare = _Provider("spare", "from the spare")
    chain = _chain(down, spare)

    assert chain.degraded is False
    chain.generate("hi")
    assert chain.degraded is True


def test_a_healthy_chain_is_not_degraded():
    chain = _chain(_Provider("good"), _Provider("spare"))
    chain.generate("hi")

    assert chain.degraded is False


# ------------------------------------------ the chain is what it says


def _chain_of(monkeypatch, primary, model, fallbacks):
    """Build the real plan chain from settings, with no network."""
    from src.ai.providers import factory

    class _Fake:
        def __init__(self, name, model=""):
            self.name = name
            self.model = model

        def generate(self, *a, **k):
            return "ok"

        def unload(self):
            pass

    monkeypatch.setattr(
        factory, "_build_chat",
        lambda p, m, s, c: _Fake(p, m),
    )
    monkeypatch.setattr(
        factory, "GeminiChatProvider", lambda c, m: _Fake("gemini", m)
    )
    monkeypatch.setattr(
        factory, "OllamaChatProvider", lambda m, url: _Fake("ollama", m)
    )

    class _Settings:
        ai_plan_provider = primary
        ai_plan_model = model
        ai_plan_fallbacks = fallbacks
        openai_api_key = "key"
        gemini_text_model = "gemini-flash-latest"
        ai_chat_model = "qwen3.5:4b"
        ollama_base_url = "http://localhost:11434"

    built = factory.build_plan_chat(_Settings(), None, _Fake("ollama", "local"))
    return [f"{p.name}:{p.model}" for p in built._providers]


def test_the_strong_model_survives_being_demoted(monkeypatch):
    """Listing openai as a FALLBACK used to drop it silently - it was
    only ever handled as the primary - so promoting the fast model
    deleted the strong one from the chain entirely."""
    rungs = _chain_of(
        monkeypatch, "gemini", "gemini-flash-lite-latest",
        ["openai", "gemini", "ollama"],
    )

    assert rungs[0] == "gemini:gemini-flash-lite-latest"
    assert any("nemotron" in r for r in rungs), rungs


def test_no_rung_appears_twice(monkeypatch):
    """Promoting one above another must not leave the loser in the list
    a second time under a different name."""
    rungs = _chain_of(
        monkeypatch, "gemini", "gemini-flash-lite-latest",
        ["openai", "gemini", "ollama"],
    )

    assert len(rungs) == len(set(rungs)), rungs


def test_the_local_model_is_always_last(monkeypatch):
    rungs = _chain_of(
        monkeypatch, "gemini", "gemini-flash-lite-latest",
        ["openai", "gemini", "ollama"],
    )

    assert rungs[-1].startswith("ollama:")
