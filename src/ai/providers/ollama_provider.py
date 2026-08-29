from __future__ import annotations

import base64

from src.ai.providers._http import post_json
from src.ai.providers.base import (
    ChatProvider,
    EmbeddingProvider,
    ProviderError,
    VisionProvider,
)

DEFAULT_BASE_URL = "http://localhost:11434"


def _clean_base(base_url: str) -> str:
    return (base_url or DEFAULT_BASE_URL).rstrip("/")


class OllamaChatProvider(ChatProvider):
    """Local text generation via a running Ollama server. No API key."""

    name = "ollama"

    def __init__(
        self,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 180.0,
        think: bool = False,
    ) -> None:
        self.model = model
        self._base = _clean_base(base_url)
        self._timeout = timeout
        # Reasoning models (qwen3.5, etc.) default to extended "thinking"
        # in Ollama, which can burn 15k+ tokens and 60s+ on a trivial
        # decision. Alfred wants fast structured answers, so thinking is
        # off unless explicitly enabled.
        self._think = think

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.4,
        max_tokens: int | None = None,
    ) -> str:
        options: dict[str, object] = {"temperature": temperature}

        if max_tokens is not None:
            options["num_predict"] = max_tokens

        payload: dict[str, object] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "think": self._think,
            "options": options,
        }

        if system:
            payload["system"] = system

        data = post_json(
            f"{self._base}/api/generate", payload, timeout=self._timeout
        )

        return str(data.get("response", "")).strip()


class OllamaEmbeddingProvider(EmbeddingProvider):
    name = "ollama"

    def __init__(
        self,
        model: str = "nomic-embed-text",
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self._base = _clean_base(base_url)
        self._timeout = timeout

    def embed(self, text: str) -> list[float] | None:
        text = text.strip()

        if not text:
            return None

        try:
            data = post_json(
                f"{self._base}/api/embeddings",
                {"model": self.model, "prompt": text},
                timeout=self._timeout,
            )
        except ProviderError as exc:
            print(f"[Embeddings/ollama] failed: {exc}")
            return None

        vector = data.get("embedding")

        if isinstance(vector, list) and vector:
            return [float(x) for x in vector]

        return None


class OllamaVisionProvider(VisionProvider):
    """Local multimodal model via Ollama (e.g. moondream, qwen2.5-vl, llava)."""

    name = "ollama"

    def __init__(
        self,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 180.0,
        think: bool = False,
    ) -> None:
        self.model = model
        self._base = _clean_base(base_url)
        self._timeout = timeout
        self._think = think

    def analyze(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mime_type: str = "image/png",
    ) -> str:
        if not image_bytes:
            raise ProviderError("Screenshot image is empty.")

        encoded = base64.b64encode(image_bytes).decode("ascii")

        data = post_json(
            f"{self._base}/api/generate",
            {
                "model": self.model,
                "prompt": prompt,
                "images": [encoded],
                "stream": False,
                "think": self._think,
            },
            timeout=self._timeout,
        )

        text = str(data.get("response", "")).strip()

        if not text:
            raise ProviderError("Ollama vision model returned no analysis.")

        return text
