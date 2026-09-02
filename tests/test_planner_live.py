"""What the planner actually does, asked of the real model.

Prompt rules cannot be tested with a fake: a fake returns whatever it
was handed, so a test built on one proves the fake works. These cost
a few model calls and are skipped when there is no key.

They exist because of a real conversation. "open how to fish" was
planned as "Learn a routine for how to fish" - the words "how to"
fired the build-a-routine rule, so the request never reached the app
launcher at all, and the reply came back as a fishing guide.
"""

from __future__ import annotations

import pytest

from src.config import load_settings


def _chat():
    from google import genai

    from src.ai.providers import build_providers

    settings = load_settings()
    bundle = build_providers(
        settings, genai.Client(api_key=settings.gemini_api_key)
    )
    return getattr(bundle, "plan_chat", None) or bundle.chat


pytestmark = pytest.mark.skipif(
    not load_settings().gemini_api_key,
    reason="no GEMINI_API_KEY - live planner tests skipped",
)


@pytest.fixture(scope="module")
def agent():
    from src.brain.agent import TaskAgent
    from src.brain.policy import Policy
    from src.tools.registry import ToolRegistry

    reg = ToolRegistry()
    policy = Policy("full", set(reg.names()), surface="voice")
    made = TaskAgent(_chat(), reg, policy, policy_voice=policy)
    made._run_knowledge = ""
    made._run_apps = ""
    return made


def _steps(agent, goal: str, extra: str = "") -> str:
    plan = agent._make_plan(goal, extra=extra)
    return " ".join(s.get("step", "") for s in plan).lower()


@pytest.mark.parametrize("goal", ["open how to fish", "play how to draw"])
def test_a_name_containing_how_to_is_still_a_thing_to_open(agent, goal):
    """The rule keys on the VERB. "Open X" opens X, whatever X is called."""
    text = _steps(agent, goal)

    assert "routine" not in text, (
        f"{goal!r} was planned as a routine to learn: {text!r}"
    )
    assert "open" in text or "launch" in text, (
        f"{goal!r} should open the thing; got {text!r}"
    )


def test_learn_how_to_still_builds_a_routine(agent):
    """The narrowing must not have cost the rule it narrows."""
    text = _steps(agent, "learn how to check the free disk space")

    assert "routine" in text, f"expected a routine, got {text!r}"


def test_a_steer_replans_around_what_was_just_said(agent):
    """Words that arrive mid-task correct the plan, not the next click.

    Told "on my desktop" while searching, it searched again and
    handed back an article. The remaining steps have to be dropped
    when they answer the question the person moved on from.
    """
    text = _steps(agent, "open how to fish", extra=(
        "Done so far: Searched the web for how to fish. "
        "THE USER HAS SINCE SAID: on my desktop "
        "They said it while this was running, so it comes AFTER the "
        "plan and overrides it. Re-plan what is left around what they "
        "said - if the remaining steps answer the wrong question now, "
        "drop them."))

    assert not any(w in text for w in ("web", "google", "browser", "online")), (
        f"it was told 'on my desktop' and still went to the web: {text!r}"
    )
