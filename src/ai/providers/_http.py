from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from src.ai.providers.base import ProviderError


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """
    Minimal JSON POST with no third-party dependency. Keeps the local
    (Ollama) and OpenAI-compatible providers dependency-free so Alfred
    can run fully offline without extra installs.
    """

    body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")

    for key, value in (headers or {}).items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:500]
        except Exception:  # noqa: BLE001
            pass
        raise ProviderError(
            f"HTTP {exc.code} from {url}: {detail or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ProviderError(
            f"Could not reach {url}: {exc.reason}. "
            "Is the local model server running?"
        ) from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Non-JSON response from {url}: {raw[:200]}") from exc

    if not isinstance(parsed, dict):
        raise ProviderError(f"Unexpected response shape from {url}")

    return parsed
