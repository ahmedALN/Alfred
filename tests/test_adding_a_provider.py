"""Adding a free provider, without trusting a model name from months ago.

Which endpoints exist is stable and public. Which models each one
serves is not, and neither is which of them can actually plan - the
NVIDIA catalogue has a model called "lightning" that answers in 8.6
seconds with the literal template it was shown. So `models add` asks
the provider what it has, times the plausible ones on the real planner
prompt, and keeps the fastest that produces a plan.
"""

from __future__ import annotations

import pytest

from src.models import (
    KNOWN_PROVIDERS,
    _looks_like_a_plan,
    _slot_for,
    _worth_timing,
    _write_env,
)

# ====================================================================
# Choosing what to measure
# ====================================================================


@pytest.mark.parametrize("model", [
    "whisper-large-v3",
    "playai-tts",
    "nomic-embed-text",
    "llama-guard-4-12b",
    "nvidia/llama-3.1-nemoguard-8b-content-safety",
    "nvidia/nemotron-4-340b-reward",
    "nvidia/nemotron-parse",
    "google/codegemma-7b",
    "microsoft/phi-3-vision-128k-instruct",
    "google/diffusiongemma-26b-a4b-it",
])
def test_things_that_are_not_planners_are_not_timed(model):
    """Every provider's list is mostly other jobs wearing the same API."""
    assert model not in _worth_timing([model, "llama-3.3-70b-versatile"])


@pytest.mark.parametrize("model", [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "openai/gpt-oss-120b",
    "qwen/qwen3-32b",
    "mistralai/codestral-22b-instruct-v0.1",
])
def test_plausible_planners_are_kept(model):
    assert model in _worth_timing([model])


def test_the_quick_sounding_ones_are_timed_first():
    """A planner that answers in under a second is the point of adding
    one, so spend the measurements there before the 70Bs."""
    order = _worth_timing([
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "qwen/qwen3-32b",
    ])

    assert order[0] == "llama-3.1-8b-instant"


def test_only_a_handful_are_timed():
    """Each one costs two real calls; a 200-model list must not become
    400 requests."""
    assert len(_worth_timing([f"model-{i}-instruct" for i in range(200)])) == 6


def test_a_list_with_nothing_usable_comes_back_empty():
    assert _worth_timing(["whisper-large", "text-embedding-3"]) == []


# ====================================================================
# Judging the answer
# ====================================================================


def test_a_model_that_echoes_the_template_has_not_planned():
    """Real, from NVIDIA's nemotron-3.5-lightning: 8.6s to say nothing."""
    assert not _looks_like_a_plan('{"step": "...", "done_when": "..."}')


def test_a_real_plan_is_recognised():
    assert _looks_like_a_plan(
        '[{"step": "Open File Explorer and navigate to the folder", '
        '"done_when": "the window is showing it"}]'
    )


def test_an_empty_answer_is_not_a_plan():
    assert not _looks_like_a_plan("")
    assert not _looks_like_a_plan("ok")


# ====================================================================
# Writing it down
# ====================================================================


def test_writing_leaves_everything_else_exactly_as_it_was(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "GEMINI_API_KEY=secret-do-not-touch\n"
        "ALFRED_AI_PLAN_FALLBACKS=openai,gemini,ollama\n"
        "\n"
        "ALFRED_BRAIN_ENABLED=true\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    _write_env(
        {
            "ALFRED_OPENAI2_BASE_URL": "https://api.groq.com/openai/v1",
            "ALFRED_OPENAI2_API_KEY": "gsk-test",
            "ALFRED_OPENAI2_MODEL": "llama-3.1-8b-instant",
        },
        "openai2,openai,gemini,ollama",
    )

    written = env.read_text(encoding="utf-8")

    assert "# a comment" in written
    assert "GEMINI_API_KEY=secret-do-not-touch" in written
    assert "ALFRED_BRAIN_ENABLED=true" in written
    assert "ALFRED_OPENAI2_MODEL=llama-3.1-8b-instant" in written
    assert "ALFRED_AI_PLAN_FALLBACKS=openai2,openai,gemini,ollama" in written
    # ...and the old fallbacks line was replaced, not duplicated.
    assert written.count("ALFRED_AI_PLAN_FALLBACKS=") == 1


def test_an_existing_value_is_updated_in_place(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "ALFRED_OPENAI2_MODEL=an-old-model\n"
        "ALFRED_AI_PLAN_FALLBACKS=openai,ollama\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    _write_env({"ALFRED_OPENAI2_MODEL": "a-better-model"}, "openai2,openai,ollama")

    written = env.read_text(encoding="utf-8")

    assert "an-old-model" not in written
    assert written.count("ALFRED_OPENAI2_MODEL=") == 1


def test_a_second_provider_gets_its_own_slot(tmp_path, monkeypatch):
    """Adding Cerebras after Groq must not overwrite Groq."""
    env = tmp_path / ".env"
    env.write_text(
        "ALFRED_OPENAI2_BASE_URL=https://api.groq.com/openai/v1\n"
        "ALFRED_OPENAI2_API_KEY=gsk-test\n"
        "ALFRED_OPENAI2_MODEL=llama-3.1-8b-instant\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert _slot_for("cerebras") == "openai3"


def test_adding_the_same_provider_again_reuses_its_slot(tmp_path, monkeypatch):
    """Re-running it after a model gets retired should update, not pile up."""
    env = tmp_path / ".env"
    env.write_text(
        f"ALFRED_OPENAI2_BASE_URL={KNOWN_PROVIDERS['groq']}\n"
        "ALFRED_OPENAI2_API_KEY=gsk-test\n"
        "ALFRED_OPENAI2_MODEL=an-old-model\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert _slot_for("groq") == "openai2"


def test_the_first_slot_is_used_when_nothing_is_configured(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("GEMINI_API_KEY=x\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert _slot_for("groq") == "openai2"


# ====================================================================
# The endpoints themselves
# ====================================================================


@pytest.mark.parametrize("name", ["groq", "cerebras", "openrouter"])
def test_the_providers_worth_adding_are_known_by_name(name):
    """So it is `models add groq <key>` rather than looking up a URL."""
    assert KNOWN_PROVIDERS[name].startswith("https://")
    assert KNOWN_PROVIDERS[name].endswith(("/v1", "/openai/v1"))


def test_an_unknown_provider_is_refused_rather_than_guessed(capsys):
    from src.models import add_provider

    assert add_provider("somethingelse", "a-key") == 2

    out = capsys.readouterr().out
    assert "groq" in out and "base URL" in out
