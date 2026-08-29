import pytest

from src.ai.providers.base import ProviderError
from src.ai.providers.fallback import FallbackChatProvider


class P:
    def __init__(self, name, answer=None, error=None):
        self.name = name
        self.model = name
        self._answer = answer
        self._error = error
        self.calls = 0

    def generate(self, prompt, **kw):
        self.calls += 1
        if self._error:
            raise self._error
        return self._answer

    def unload(self):
        pass


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_uses_primary_when_it_works():
    a, b = P("a", "from a"), P("b", "from b")
    chain = FallbackChatProvider([a, b])
    assert chain.generate("x") == "from a"
    assert b.calls == 0


def test_falls_through_to_next_on_error():
    a = P("a", error=ProviderError("429 RESOURCE_EXHAUSTED"))
    b = P("b", "from b")
    c = P("c", "from c")
    chain = FallbackChatProvider([a, b, c])
    assert chain.generate("x") == "from b"
    assert c.calls == 0


def test_sticks_to_the_fallback_then_retries_primary_after_cooldown():
    clk = Clock()
    a = P("a", error=ProviderError("quota"))
    b = P("b", "from b")
    chain = FallbackChatProvider([a, b], retry_primary_after=600, monotonic=clk)

    assert chain.generate("x") == "from b"  # a fails, b answers
    a.calls = 0

    clk.t = 100
    assert chain.generate("x") == "from b"
    assert a.calls == 0  # still cooling down, primary not retried

    clk.t = 700  # past the cooldown
    a._error = None
    a._answer = "from a again"
    assert chain.generate("x") == "from a again"


def test_all_failing_raises():
    chain = FallbackChatProvider([
        P("a", error=ProviderError("x")),
        P("b", error=RuntimeError("y")),
    ])
    with pytest.raises(ProviderError):
        chain.generate("x")


def test_empty_answer_is_treated_as_failure():
    a, b = P("a", ""), P("b", "real answer")
    assert FallbackChatProvider([a, b]).generate("x") == "real answer"


def test_needs_at_least_one_provider():
    with pytest.raises(ProviderError):
        FallbackChatProvider([])


def test_dead_rung_is_skipped_on_the_next_call():
    clk = Clock()
    a = P("a", error=ProviderError("403 forbidden"))
    b = P("b", "from b")
    chain = FallbackChatProvider([a, b], retry_primary_after=600, monotonic=clk)

    assert chain.generate("x") == "from b"
    assert a.calls == 1
    clk.t = 5
    assert chain.generate("x") == "from b"
    assert a.calls == 1  # not retried - still on cooldown


def test_everything_cooling_down_falls_back_to_last():
    clk = Clock()
    a = P("a", error=ProviderError("down"))
    b = P("b", error=ProviderError("down"))
    c = P("c", "local model")
    chain = FallbackChatProvider([a, b, c], retry_primary_after=600, monotonic=clk)

    assert chain.generate("x") == "local model"
    # a and b cooled down; c is fine. next call skips a and b, hits c.
    a.calls = b.calls = c.calls = 0
    clk.t = 10
    # force c onto cooldown too
    c._error = ProviderError("blip")
    with pytest.raises(ProviderError):
        chain.generate("x")
    # now everything is cooling; recovery path clears cooldowns and tries last
    c._error = None
    c._answer = "recovered"
    clk.t = 20
    assert chain.generate("x") == "recovered"
