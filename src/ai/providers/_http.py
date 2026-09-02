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

# Say who is calling.
#
# urllib sends "Python-urllib/3.13" when nothing else is set, and the
# bot protection in front of several providers refuses that outright:
# Groq answered 403 Forbidden to a key that curl got a 200 with, and
# Cerebras the same. Not a rate limit, not a bad key - the request
# never reached the API at all.
#
# That would have been a quiet disaster. The key would go in, the model
# would be configured, the rung would sit in the chain returning 403 on
# every call, and the failover would route around it forever without
# anybody learning why.
_USER_AGENT = "Alfred/0.1 (+https://github.com/ahmedALN/Alfred)"


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
    request.add_header("User-Agent", _USER_AGENT)

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
