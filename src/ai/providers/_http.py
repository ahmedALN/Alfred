from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from src.ai.providers.base import ProviderError

# HTTP statuses that are usually transient - worth one quick retry before
# giving up (and, for the fallback chain, before failing over).
_RETRYABLE = {429, 500, 502, 503, 504}


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 120.0,
    retries: int = 2,
) -> dict[str, Any]:
    """
    Minimal JSON POST with no third-party dependency. Keeps the local
    (Ollama) and OpenAI-compatible providers dependency-free so Alfred
    can run fully offline without extra installs. Retries transient
    HTTP errors (429/5xx) with a short backoff.
    """

    body = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")

    for key, value in (headers or {}).items():
        request.add_header(key, value)

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
            break
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")[:500]
            except Exception:  # noqa: BLE001
                pass
            last_err = ProviderError(
                f"HTTP {exc.code} from {url}: {detail or exc.reason}"
            )
            if exc.code in _RETRYABLE and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise last_err from exc
        except urllib.error.URLError as exc:
            last_err = ProviderError(
                f"Could not reach {url}: {exc.reason}. "
                "Is the local model server running?"
            )
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise last_err from exc
    else:  # pragma: no cover - loop always breaks or raises
        raise last_err or ProviderError(f"POST {url} failed")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Non-JSON response from {url}: {raw[:200]}") from exc

    if not isinstance(parsed, dict):
        raise ProviderError(f"Unexpected response shape from {url}")

    return parsed
