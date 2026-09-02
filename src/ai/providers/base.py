from __future__ import annotations

import re
from abc import ABC, abstractmethod


class ProviderError(RuntimeError):
    """Raised when an AI provider call fails."""


_THINK_RE = re.compile(
    r"<(think|thinking|reasoning)>.*?</\1>", re.IGNORECASE | re.DOTALL
)

# Tagless reasoning preambles some models emit even with thinking off,
# e.g. nemotron: "We need to produce a plan... Steps: ... {answer}".
_PREAMBLE_RE = re.compile(
    r"^\s*(here('?s| is)|let me|okay|first|i need to|i'?ll|i will|we need|"
    r"we should|we can|the user|thinking|to (solve|do) this|analy[sz]|"
    r"step\s*1|1[.)]\s)",
    re.IGNORECASE,
)


def strip_reasoning(text: str) -> str:
    """Drop <think>...</think> blocks and tagless reasoning preambles that
    reasoning models (qwen3.x, Nemotron, DeepSeek-R1, ...) emit even with
    thinking disabled, so downstream JSON parsing stays reliable."""
    if not text:
        return text
    cleaned = _THINK_RE.sub("", text)
    low = cleaned.lower()
    if "<think>" in low and "</think>" not in low:
        cleaned = cleaned[: low.rfind("<think>")]
    cleaned = cleaned.strip()

    # Analytical preamble before a JSON/array payload -> keep the payload.
    if not cleaned.startswith(("{", "[")) and _PREAMBLE_RE.match(cleaned):
        starts = [i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1]
        end = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if starts:
            brace = min(starts)
            if 0 <= brace < end:
                cleaned = cleaned[brace : end + 1]

    return cleaned.strip()


class _Unloadable:
    """Mixin: free any GPU/RAM the backend is holding. No-op for cloud APIs."""

    def unload(self) -> None:
        return None


class ChatProvider(_Unloadable, ABC):
    """Text in, text out. Used for memory distillation and the brain's reasoning."""

    name: str = "chat"
    model: str = ""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.4,
        max_tokens: int | None = None,
    ) -> str:
        """
        Return the model's text response, stripped. Should raise
        ProviderError on a hard failure so callers can decide whether
        to degrade or surface the error.
        """
        raise NotImplementedError


class EmbeddingProvider(_Unloadable, ABC):
    """Text to vector. Used for memory dedup and semantic recall."""

    name: str = "embedding"
    model: str = ""

    @abstractmethod
    def embed(self, text: str) -> list[float] | None:
        """Return an embedding vector, or None if unavailable."""
        raise NotImplementedError


class VisionProvider(_Unloadable, ABC):
    """Image + prompt to text. Used to describe the desktop Alfred controls."""

    name: str = "vision"
    model: str = ""

    @abstractmethod
    def analyze(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mime_type: str = "image/png",
    ) -> str:
        """Return a textual analysis of the image. Raise ProviderError on failure."""
        raise NotImplementedError
