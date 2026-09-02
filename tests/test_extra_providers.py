"""One endpoint meant one free tier.

`ALFRED_OPENAI_BASE_URL` was singular, so Alfred could use NVIDIA or
Groq or Cerebras and never two of them. That is the wrong shape for how
these are actually used: the free tiers are all rate-limited, they run
out at different times of day, and somewhere to go when one does is the
entire point of a fallback chain. Measured on this machine with the
Gemini tier spent, the chain was one working cloud rung and the local
4B - and the 4B was the faster of the two.
"""

from __future__ import annotations

import pytest

from src.ai.providers.factory import _EXTRA_ENDPOINT, _extra_openai


@pytest.mark.parametrize("name", ["openai2", "openai3", "openai42"])
def test_numbered_endpoints_are_recognised(name):
    assert _EXTRA_ENDPOINT.fullmatch(name)


@pytest.mark.parametrize("name", ["openai", "gemini", "ollama", "openaix", ""])
def test_everything_else_is_not(name):
    """`openai` on its own is the original endpoint and is handled
    separately; the rest are other providers entirely."""
    assert not _EXTRA_ENDPOINT.fullmatch(name)


def test_an_unconfigured_endpoint_is_simply_absent(monkeypatch):
    for suffix in ("BASE_URL", "API_KEY", "MODEL"):
        monkeypatch.delenv(f"ALFRED_OPENAI2_{suffix}", raising=False)

    assert _extra_openai("openai2") is None


@pytest.mark.parametrize("missing", ["BASE_URL", "API_KEY", "MODEL"])
def test_a_half_configured_endpoint_is_absent_rather_than_broken(
    monkeypatch, missing
):
    """A chain rung that exists but cannot work is worse than one that
    does not exist: it costs a failed round trip on every call."""
    for suffix, value in (
        ("BASE_URL", "https://api.example.com/v1"),
        ("API_KEY", "test-key"),
        ("MODEL", "some-model"),
    ):
        if suffix == missing:
            monkeypatch.delenv(f"ALFRED_OPENAI2_{suffix}", raising=False)
        else:
            monkeypatch.setenv(f"ALFRED_OPENAI2_{suffix}", value)

    assert _extra_openai("openai2") is None


def test_a_configured_endpoint_is_built(monkeypatch):
    monkeypatch.setenv("ALFRED_OPENAI2_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("ALFRED_OPENAI2_API_KEY", "gsk-test")
    monkeypatch.setenv("ALFRED_OPENAI2_MODEL", "llama-3.3-70b-versatile")

    provider = _extra_openai("openai2")

    assert provider is not None
    assert provider.model == "llama-3.3-70b-versatile"


def test_two_extra_endpoints_are_independent(monkeypatch):
    monkeypatch.setenv("ALFRED_OPENAI2_BASE_URL", "https://a.example.com/v1")
    monkeypatch.setenv("ALFRED_OPENAI2_API_KEY", "key-a")
    monkeypatch.setenv("ALFRED_OPENAI2_MODEL", "model-a")
    monkeypatch.setenv("ALFRED_OPENAI3_BASE_URL", "https://b.example.com/v1")
    monkeypatch.setenv("ALFRED_OPENAI3_API_KEY", "key-b")
    monkeypatch.setenv("ALFRED_OPENAI3_MODEL", "model-b")

    assert _extra_openai("openai2").model == "model-a"
    assert _extra_openai("openai3").model == "model-b"


def test_an_extra_endpoint_joins_the_planning_chain(monkeypatch):
    """End to end: named in the fallbacks, it appears in the chain."""
    monkeypatch.setenv("ALFRED_OPENAI2_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("ALFRED_OPENAI2_API_KEY", "gsk-test")
    monkeypatch.setenv("ALFRED_OPENAI2_MODEL", "llama-3.3-70b-versatile")

    from src.ai.providers.factory import build_plan_chat
    from src.ai.providers.ollama_provider import OllamaChatProvider

    class _Settings:
        ai_plan_provider = "ollama"
        ai_plan_model = "qwen3.5:4b"
        ai_plan_fallbacks = ["openai2"]
        ai_chat_model = "qwen3.5:4b"
        ollama_base_url = "http://localhost:11434"
        openai_api_key = ""
        openai_base_url = ""
        gemini_text_model = "gemini-flash-latest"

    local = OllamaChatProvider("qwen3.5:4b", "http://localhost:11434")
    chain = build_plan_chat(_Settings(), None, local)

    models = [getattr(p, "model", "") for p in getattr(chain, "_providers", [chain])]

    assert "llama-3.3-70b-versatile" in models
    # ...and the local model is still the safety net at the end.
    assert models[-1] == "qwen3.5:4b"


# ====================================================================
# Holding the local model in memory
# ====================================================================


def test_the_local_model_is_asked_to_stay_loaded():
    """Ollama drops a model five minutes after the last request, so the
    first thing asked after a quiet spell paid for a 3.3GB reload -
    2.10s warm against 5.11s cold, landing on exactly the request you
    notice."""
    from src.ai.providers.ollama_provider import OllamaChatProvider

    sent = {}

    def _fake_post(url, payload, timeout):
        sent.update(payload)
        return {"response": "ok"}

    import src.ai.providers.ollama_provider as module

    original = module.post_json
    module.post_json = _fake_post
    try:
        OllamaChatProvider("qwen3.5:4b").generate("hi")
    finally:
        module.post_json = original

    assert sent["keep_alive"] == "30m"


def test_how_long_it_stays_is_configurable(monkeypatch):
    monkeypatch.setenv("ALFRED_OLLAMA_KEEP_ALIVE", "5s")

    from src.ai.providers.ollama_provider import OllamaChatProvider

    assert OllamaChatProvider("qwen3.5:4b")._keep_alive == "5s"


def test_game_mode_can_still_take_the_gpu_back():
    """Holding the model must not fight "free up the GPU" - the unload
    is explicit and says keep_alive 0."""
    import src.ai.providers.ollama_provider as module

    sent = {}

    def _fake_post(url, payload, timeout):
        sent.update(payload)
        return {}

    original = module.post_json
    module.post_json = _fake_post
    try:
        module.OllamaChatProvider("qwen3.5:4b").unload()
    finally:
        module.post_json = original

    assert sent["keep_alive"] == 0


def test_the_local_model_is_last_not_merely_present():
    """The guarantee is a net at the BOTTOM.

    The check asked whether a local model was anywhere in the chain,
    which stopped being the same thing once the chain could be arranged
    freely. Put ollama first and a cloud provider after it and the last
    rung was one that can rate-limit, with nothing underneath it.
    """
    from src.ai.providers.factory import build_plan_chat
    from src.ai.providers.ollama_provider import OllamaChatProvider

    class _Settings:
        ai_plan_provider = "ollama"
        ai_plan_model = "qwen3.5:4b"
        ai_plan_fallbacks = ["openai2"]
        ai_chat_model = "qwen3.5:4b"
        ollama_base_url = "http://localhost:11434"
        openai_api_key = ""
        openai_base_url = ""
        gemini_text_model = "gemini-flash-latest"

    import os

    os.environ["ALFRED_OPENAI2_BASE_URL"] = "https://api.groq.com/openai/v1"
    os.environ["ALFRED_OPENAI2_API_KEY"] = "gsk-test"
    os.environ["ALFRED_OPENAI2_MODEL"] = "llama-3.3-70b-versatile"
    try:
        local = OllamaChatProvider("qwen3.5:4b", "http://localhost:11434")
        chain = build_plan_chat(_Settings(), None, local)
        rungs = list(getattr(chain, "_providers", [chain]))
    finally:
        for suffix in ("BASE_URL", "API_KEY", "MODEL"):
            os.environ.pop(f"ALFRED_OPENAI2_{suffix}", None)

    assert getattr(rungs[-1], "name", "") == "ollama", (
        "the chain ends on something that can rate-limit"
    )
