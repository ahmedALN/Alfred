from __future__ import annotations

from abc import ABC, abstractmethod


class ProviderError(RuntimeError):
    """Raised when an AI provider call fails."""


class ChatProvider(ABC):
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


class EmbeddingProvider(ABC):
    """Text to vector. Used for memory dedup and semantic recall."""

    name: str = "embedding"
    model: str = ""

    @abstractmethod
    def embed(self, text: str) -> list[float] | None:
        """Return an embedding vector, or None if unavailable."""
        raise NotImplementedError


class VisionProvider(ABC):
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
