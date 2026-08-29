from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.ai.providers.base import (
    ChatProvider,
    EmbeddingProvider,
    ProviderError,
    VisionProvider,
)
from src.ai.providers.gemini_provider import (
    GeminiChatProvider,
    GeminiEmbeddingProvider,
    GeminiVisionProvider,
)
from src.ai.providers.ollama_provider import (
    OllamaChatProvider,
    OllamaEmbeddingProvider,
    OllamaVisionProvider,
)
from src.ai.providers.openai_provider import (
    OpenAICompatibleChatProvider,
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleVisionProvider,
)

SUPPORTED = ("gemini", "ollama", "openai")

# Per-capability fallback models used when the operator does not set an
# explicit *_MODEL. Keys are (provider, capability).
_DEFAULT_MODELS: dict[tuple[str, str], str] = {
    ("gemini", "embed"): "text-embedding-004",
    ("gemini", "vision"): "gemini-2.5-flash",
    ("ollama", "chat"): "qwen3.5",
    ("ollama", "embed"): "nomic-embed-text",
    ("ollama", "vision"): "moondream",
}


@dataclass(frozen=True)
class ProviderBundle:
    chat: ChatProvider
    embedder: EmbeddingProvider
    vision: VisionProvider

    def describe(self) -> str:
        return (
            f"chat={self.chat.name}:{self.chat.model or '?'} "
            f"embed={self.embedder.name}:{self.embedder.model or '?'} "
            f"vision={self.vision.name}:{self.vision.model or '?'}"
        )


def _resolve(name: str | None, fallback: str) -> str:
    chosen = (name or fallback or "gemini").strip().lower()

    if chosen not in SUPPORTED:
        raise ProviderError(
            f"Unknown AI provider {chosen!r}. Supported: {', '.join(SUPPORTED)}."
        )

    return chosen


def _model_for(
    provider: str,
    capability: str,
    explicit: str | None,
    gemini_chat_default: str,
) -> str:
    if explicit:
        return explicit

    if provider == "gemini" and capability == "chat":
        return gemini_chat_default

    return _DEFAULT_MODELS.get((provider, capability), "")


def build_providers(settings: Any, gemini_client: Any) -> ProviderBundle:
    """
    Construct the chat / embedding / vision providers from settings.

    ``ALFRED_AI_PROVIDER`` picks the default backend for all three;
    ``ALFRED_AI_CHAT_PROVIDER`` / ``_EMBED_PROVIDER`` / ``_VISION_PROVIDER``
    override individual capabilities. Voice always stays on Gemini Live
    and is not routed through here.
    """

    default = _resolve(settings.ai_provider, "gemini")

    chat_provider = _resolve(settings.ai_chat_provider, default)
    embed_provider = _resolve(settings.ai_embed_provider, default)
    vision_provider = _resolve(settings.ai_vision_provider, default)

    gemini_chat_default = settings.gemini_text_model

    chat = _build_chat(
        chat_provider,
        _model_for(
            chat_provider, "chat", settings.ai_chat_model, gemini_chat_default
        ),
        settings,
        gemini_client,
    )

    embedder = _build_embed(
        embed_provider,
        _model_for(
            embed_provider, "embed", settings.ai_embed_model, gemini_chat_default
        ),
        settings,
        gemini_client,
    )

    vision = _build_vision(
        vision_provider,
        _model_for(
            vision_provider,
            "vision",
            settings.ai_vision_model,
            gemini_chat_default,
        ),
        settings,
        gemini_client,
    )

    return ProviderBundle(chat=chat, embedder=embedder, vision=vision)


def _build_chat(
    provider: str, model: str, settings: Any, gemini_client: Any
) -> ChatProvider:
    if provider == "gemini":
        return GeminiChatProvider(gemini_client, model)

    if provider == "ollama":
        return OllamaChatProvider(model, settings.ollama_base_url)

    return OpenAICompatibleChatProvider(
        model, settings.openai_base_url or "", settings.openai_api_key
    )


def _build_embed(
    provider: str, model: str, settings: Any, gemini_client: Any
) -> EmbeddingProvider:
    if provider == "gemini":
        return GeminiEmbeddingProvider(gemini_client, model)

    if provider == "ollama":
        return OllamaEmbeddingProvider(model, settings.ollama_base_url)

    return OpenAICompatibleEmbeddingProvider(
        model, settings.openai_base_url or "", settings.openai_api_key
    )


def _build_vision(
    provider: str, model: str, settings: Any, gemini_client: Any
) -> VisionProvider:
    if provider == "gemini":
        return GeminiVisionProvider(gemini_client, model)

    if provider == "ollama":
        return OllamaVisionProvider(model, settings.ollama_base_url)

    return OpenAICompatibleVisionProvider(
        model, settings.openai_base_url or "", settings.openai_api_key
    )
