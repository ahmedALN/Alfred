from __future__ import annotations

import math

from google import genai


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0

    dot = sum(x * y for x, y in zip(a, b))

    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot / (norm_a * norm_b)


class EmbeddingClient:
    """
    Thin wrapper around the Gemini embedding endpoint.

    Kept isolated so the embedding model name can change (or the
    call can be swapped for a local model) without touching the
    memory store or learner logic.
    """

    def __init__(
        self,
        client: genai.Client,
        model: str = "text-embedding-004",
    ) -> None:
        self._client = client
        self._model = model

    def embed(self, text: str) -> list[float] | None:
        text = text.strip()

        if not text:
            return None

        try:
            response = self._client.models.embed_content(
                model=self._model,
                contents=text,
            )
        except Exception as exc:
            print(f"[Embeddings] failed to embed text: {exc}")
            return None

        embeddings = getattr(response, "embeddings", None)

        if embeddings:
            values = getattr(embeddings[0], "values", None)

            if values:
                return list(values)

        # Fallback shape used by some SDK versions.
        embedding = getattr(response, "embedding", None)
        values = getattr(embedding, "values", None)

        if values:
            return list(values)

        return None
