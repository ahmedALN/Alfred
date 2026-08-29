from __future__ import annotations

import time
from typing import Callable

from src.ai.providers.base import ChatProvider, ProviderError


class FallbackChatProvider(ChatProvider):
    """
    An ordered chain of chat providers. Uses the first that works; when
    it errors (rate limit, quota, network, bad response) it moves to the
    next and sticks there, retrying the primary again after a cooldown.

    Built for: NVIDIA Nemotron (free, credit-limited) -> Gemini flash ->
    local qwen, so planning keeps working when the good model runs out.
    """

    def __init__(
        self,
        providers: list[ChatProvider],
        *,
        retry_primary_after: float = 600.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers = [p for p in providers if p is not None]
        if not self._providers:
            raise ProviderError("FallbackChatProvider needs at least one provider.")

        self._retry_after = retry_primary_after
        self._monotonic = monotonic
        self._active = 0
        self._primary_failed_at = 0.0

        self.name = "chain(" + ">".join(
            f"{p.name}:{getattr(p, 'model', '?')}" for p in self._providers
        ) + ")"
        self.model = getattr(self._providers[0], "model", "")

    # ----------------------------------------------------------------

    @property
    def active_name(self) -> str:
        p = self._providers[self._active]
        return f"{p.name}:{getattr(p, 'model', '?')}"

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.4,
        max_tokens: int | None = None,
    ) -> str:
        # Enough time passed since the primary failed -> give it another go.
        if (
            self._active > 0
            and self._monotonic() - self._primary_failed_at > self._retry_after
        ):
            self._active = 0

        errors: list[str] = []

        for i in range(self._active, len(self._providers)):
            provider = self._providers[i]
            try:
                out = provider.generate(
                    prompt, system=system, temperature=temperature,
                    max_tokens=max_tokens,
                )
                if out and out.strip():
                    if i != self._active:
                        print(f"[Plan] using {provider.name} (fell back from index {self._active})")
                        self._active = i
                    return out
                errors.append(f"{provider.name}: empty response")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{provider.name}: {exc}")
                self._record_failover(provider.name)

            if i == 0:
                self._primary_failed_at = self._monotonic()

        raise ProviderError(
            "every plan provider failed - " + " | ".join(errors)
        )

    def _record_failover(self, name: str) -> None:
        try:
            from src.usage import USAGE

            USAGE.record_error(f"plan_failover:{name}")
        except Exception:  # noqa: BLE001
            pass

    def unload(self) -> None:
        for p in self._providers:
            try:
                p.unload()
            except Exception:  # noqa: BLE001
                pass
