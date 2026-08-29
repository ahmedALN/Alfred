from __future__ import annotations

import base64

from src.ai.providers._http import post_json
from src.ai.providers.base import (
    ChatProvider,
    EmbeddingProvider,
    ProviderError,
    VisionProvider,
    strip_reasoning,
)

# Works with any OpenAI-compatible endpoint: NVIDIA NIM
# (https://integrate.api.nvidia.com/v1), vLLM, LM Studio, llama.cpp
# server, Groq, OpenAI itself, etc. base_url should include the
# trailing "/v1" the server expects.


def _clean_base(base_url: str) -> str:
    return (base_url or "").rstrip("/")


def _auth_headers(api_key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


class OpenAICompatibleChatProvider(ChatProvider):
    name = "openai"

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 180.0,
    ) -> None:
        self.model = model
        self._base = _clean_base(base_url)
        self._api_key = api_key
        self._timeout = timeout

        if not self._base:
            raise ProviderError(
                "openai-compatible provider needs ALFRED_OPENAI_BASE_URL."
            )

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.4,
        max_tokens: int | None = None,
    ) -> str:
        messages = []

        if system:
            messages.append({"role": "system", "content": system})

        messages.append({"role": "user", "content": prompt})

        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }

        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        data = post_json(
            f"{self._base}/chat/completions",
            payload,
            headers=_auth_headers(self._api_key),
            timeout=self._timeout,
        )

        return strip_reasoning(_first_choice_text(data))


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    name = "openai"

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self._base = _clean_base(base_url)
        self._api_key = api_key
        self._timeout = timeout

    def embed(self, text: str) -> list[float] | None:
        text = text.strip()

        if not text or not self._base:
            return None

        try:
            data = post_json(
                f"{self._base}/embeddings",
                {"model": self.model, "input": text},
                headers=_auth_headers(self._api_key),
                timeout=self._timeout,
            )
        except ProviderError as exc:
            print(f"[Embeddings/openai] failed: {exc}")
            return None

        items = data.get("data")

        if isinstance(items, list) and items:
            vector = items[0].get("embedding")
            if isinstance(vector, list) and vector:
                return [float(x) for x in vector]

        return None


class OpenAICompatibleVisionProvider(VisionProvider):
    name = "openai"

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 180.0,
    ) -> None:
        self.model = model
        self._base = _clean_base(base_url)
        self._api_key = api_key
        self._timeout = timeout

    def analyze(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mime_type: str = "image/png",
    ) -> str:
        if not image_bytes:
            raise ProviderError("Screenshot image is empty.")

        data_uri = (
            f"data:{mime_type};base64,"
            + base64.b64encode(image_bytes).decode("ascii")
        )

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_uri},
                        },
                    ],
                }
            ],
        }

        data = post_json(
            f"{self._base}/chat/completions",
            payload,
            headers=_auth_headers(self._api_key),
            timeout=self._timeout,
        )

        text = _first_choice_text(data)

        if not text:
            raise ProviderError("Vision model returned no analysis.")

        return text


def _first_choice_text(data: dict) -> str:
    choices = data.get("choices")

    if not isinstance(choices, list) or not choices:
        raise ProviderError(f"No choices in response: {str(data)[:200]}")

    message = choices[0].get("message", {})
    content = message.get("content", "")

    if isinstance(content, list):
        # Some servers return content as a list of parts.
        content = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict)
        )

    return str(content).strip()
