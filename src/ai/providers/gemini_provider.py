from __future__ import annotations

from typing import Any

from google.genai import types

from src.ai.providers.base import (
    ChatProvider,
    EmbeddingProvider,
    ProviderError,
    VisionProvider,
)
from src.usage import USAGE, record_response


class GeminiChatProvider(ChatProvider):
    name = "gemini"

    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self.model = model

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.4,
        max_tokens: int | None = None,
    ) -> str:
        config = types.GenerateContentConfig(
            temperature=temperature,
            system_instruction=system,
            max_output_tokens=max_tokens,
        )

        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"Gemini generate_content failed: {exc}") from exc

        record_response(response)
        return (getattr(response, "text", None) or "").strip()


class GeminiEmbeddingProvider(EmbeddingProvider):
    name = "gemini"

    def __init__(self, client: Any, model: str = "gemini-embedding-001") -> None:
        self._client = client
        self.model = model

    def embed(self, text: str) -> list[float] | None:
        text = text.strip()

        if not text:
            return None

        try:
            response = self._client.models.embed_content(
                model=self.model,
                contents=text,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[Embeddings/gemini] failed: {exc}")
            return None

        USAGE.record()
        embeddings = getattr(response, "embeddings", None)

        if embeddings:
            values = getattr(embeddings[0], "values", None)
            if values:
                return list(values)

        embedding = getattr(response, "embedding", None)
        values = getattr(embedding, "values", None)

        if values:
            return list(values)

        return None


class GeminiVisionProvider(VisionProvider):
    name = "gemini"

    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self.model = model

    def analyze(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mime_type: str = "image/png",
    ) -> str:
        if not image_bytes:
            raise ProviderError("Screenshot image is empty.")

        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=[
                    prompt,
                    types.Part.from_bytes(
                        data=image_bytes,
                        mime_type=mime_type,
                    ),
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                f"Gemini vision request failed: {type(exc).__name__}: {exc}"
            ) from exc

        record_response(response)
        text = (getattr(response, "text", None) or "").strip()

        if not text:
            raise ProviderError("Gemini vision returned no textual analysis.")

        return text
