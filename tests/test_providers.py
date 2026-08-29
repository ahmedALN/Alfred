from types import SimpleNamespace

import pytest

from src.ai.providers import build_providers
from src.ai.providers.base import ProviderError
from src.ai.providers.ollama_provider import (
    OllamaChatProvider,
    OllamaEmbeddingProvider,
)
from src.ai.providers.openai_provider import OpenAICompatibleChatProvider


def _settings(**overrides):
    base = dict(
        gemini_text_model="gemini-flash-latest",
        ai_provider="gemini",
        ai_chat_provider=None,
        ai_embed_provider=None,
        ai_vision_provider=None,
        ai_chat_model=None,
        ai_embed_model=None,
        ai_vision_model=None,
        ai_plan_provider="ollama",
        ai_plan_model="qwen3.5:9b",
        ai_plan_fallbacks=["ollama"],
        ollama_base_url="http://localhost:11434",
        openai_base_url=None,
        openai_api_key=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeGeminiClient:
    class models:
        pass


# ---------------------------------------------------------------- factory


def test_default_is_all_gemini():
    bundle = build_providers(_settings(), _FakeGeminiClient())

    assert bundle.chat.name == "gemini"
    assert bundle.embedder.name == "gemini"
    assert bundle.vision.name == "gemini"
    assert bundle.chat.model == "gemini-flash-latest"
    assert bundle.embedder.model == "gemini-embedding-001"


def test_switch_everything_to_ollama():
    bundle = build_providers(_settings(ai_provider="ollama"), _FakeGeminiClient())

    assert bundle.chat.name == "ollama"
    assert bundle.chat.model == "qwen3.5"
    assert bundle.embedder.model == "nomic-embed-text"
    assert bundle.vision.model == "moondream"


def test_per_capability_override():
    bundle = build_providers(
        _settings(
            ai_provider="gemini",
            ai_vision_provider="ollama",
            ai_vision_model="qwen2.5-vl",
        ),
        _FakeGeminiClient(),
    )

    assert bundle.chat.name == "gemini"
    assert bundle.vision.name == "ollama"
    assert bundle.vision.model == "qwen2.5-vl"


def test_openai_compatible_needs_base_url():
    bundle_settings = _settings(ai_provider="openai", ai_chat_model="x")
    with pytest.raises(ProviderError):
        build_providers(bundle_settings, _FakeGeminiClient())


def test_unknown_provider_raises():
    with pytest.raises(ProviderError):
        build_providers(_settings(ai_provider="banana"), _FakeGeminiClient())


# ---------------------------------------------------------------- ollama


def test_ollama_chat_posts_prompt(monkeypatch):
    captured = {}

    def fake_post(url, payload, **kw):
        captured["url"] = url
        captured["payload"] = payload
        return {"response": "  hello world  "}

    monkeypatch.setattr(
        "src.ai.providers.ollama_provider.post_json", fake_post
    )

    provider = OllamaChatProvider("qwen3.5")
    out = provider.generate("hi", system="be terse", temperature=0.1)

    assert out == "hello world"
    assert captured["url"].endswith("/api/generate")
    assert captured["payload"]["system"] == "be terse"
    assert captured["payload"]["options"]["temperature"] == 0.1


def test_ollama_embed_returns_vector(monkeypatch):
    monkeypatch.setattr(
        "src.ai.providers.ollama_provider.post_json",
        lambda url, payload, **kw: {"embedding": [0.1, 0.2, 0.3]},
    )

    vec = OllamaEmbeddingProvider().embed("some text")
    assert vec == [0.1, 0.2, 0.3]


def test_ollama_embed_handles_failure(monkeypatch):
    def boom(*a, **kw):
        raise ProviderError("connection refused")

    monkeypatch.setattr("src.ai.providers.ollama_provider.post_json", boom)

    assert OllamaEmbeddingProvider().embed("x") is None


# ---------------------------------------------------------------- openai


def test_openai_chat_parses_choices(monkeypatch):
    monkeypatch.setattr(
        "src.ai.providers.openai_provider.post_json",
        lambda url, payload, **kw: {
            "choices": [{"message": {"content": "answer"}}]
        },
    )

    provider = OpenAICompatibleChatProvider(
        "meta/llama-3.1-70b-instruct",
        "https://integrate.api.nvidia.com/v1",
        api_key="nvapi-xxx",
    )

    assert provider.generate("q") == "answer"
