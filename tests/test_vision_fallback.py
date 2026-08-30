"""Screenshots are the last resort, so the model reading them must see."""

import pytest

from src.ai.providers.base import ProviderError, VisionProvider
from src.ai.providers.fallback import FallbackVisionProvider, _is_useless


class _Vision(VisionProvider):
    def __init__(self, name, answer=None, boom=None):
        self.name = name
        self.model = "m"
        self.calls = 0
        self._answer = answer
        self._boom = boom

    def analyze(self, image_bytes, prompt, *, mime_type="image/png"):
        self.calls += 1
        if self._boom:
            raise ProviderError(self._boom)
        return self._answer

    def unload(self):
        pass


PLACEHOLDERS = "\n".join("<name> - center at (1, 2)" for _ in range(12))
REAL = "Add Instance button - center at (91, 44)\nSettings - center at (343, 44)"


def test_a_model_that_echoes_the_template_is_not_an_answer():
    """qwen3.5 returned fifty-one lines of literal "<name>". Passing that
    on is worse than failing: it looks exactly like a description."""
    assert _is_useless(PLACEHOLDERS)
    assert _is_useless("")
    assert not _is_useless(REAL)


def test_the_chain_moves_past_a_model_that_cannot_really_see():
    blind, seeing = _Vision("local", PLACEHOLDERS), _Vision("cloud", REAL)

    out = FallbackVisionProvider([blind, seeing]).analyze(b"png", "prompt")

    assert out == REAL
    assert blind.calls == 1 and seeing.calls == 1


def test_a_useless_rung_is_not_tried_again_straight_away():
    """One wasted call, not one per screenshot."""
    blind, seeing = _Vision("local", PLACEHOLDERS), _Vision("cloud", REAL)
    chain = FallbackVisionProvider([blind, seeing])

    for _ in range(4):
        chain.analyze(b"png", "prompt")

    assert blind.calls == 1
    assert seeing.calls == 4


def test_an_erroring_rung_is_skipped_too():
    down, up = _Vision("down", boom="503 overloaded"), _Vision("up", REAL)

    assert FallbackVisionProvider([down, up]).analyze(b"p", "q") == REAL


def test_when_nothing_can_see_it_says_so_rather_than_inventing():
    blind = _Vision("local", PLACEHOLDERS)
    down = _Vision("cloud", boom="no network")

    with pytest.raises(ProviderError, match="no vision model"):
        FallbackVisionProvider([blind, down]).analyze(b"p", "q")


def test_a_working_first_choice_is_used_and_nothing_else_is_called():
    good, spare = _Vision("good", REAL), _Vision("spare", REAL)

    FallbackVisionProvider([good, spare]).analyze(b"p", "q")

    assert good.calls == 1 and spare.calls == 0


def test_a_chain_needs_something_in_it():
    with pytest.raises(ProviderError):
        FallbackVisionProvider([])
