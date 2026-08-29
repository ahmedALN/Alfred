from __future__ import annotations

import time
from typing import Callable

from src.ai.providers.base import ChatProvider, ProviderError


class FallbackChatProvider(ChatProvider):
    """
    An ordered chain of chat providers. Uses the earliest one that works;
    a provider that errors (rate limit, quota, network, bad response) is
    put on a per-provider cooldown and skipped until it expires, so a
    persistently-down rung (e.g. an unentitled NVIDIA key) doesn't cost a
    failed round-trip on every call.

    Built for: NVIDIA Nemotron -> Gemini flash -> Gemini flash-lite ->
    local qwen, so planning keeps working when the good models are down.
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
        # index -> monotonic time the provider is on cooldown until
        self._cooldown_until: dict[int, float] = {}

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
        now = self._monotonic()
        errors: list[str] = []
        tried = False

        for i, provider in enumerate(self._providers):
            if self._cooldown_until.get(i, 0.0) > now:
                continue  # still cooling down from a recent failure

            tried = True
            try:
                out = provider.generate(
                    prompt, system=system, temperature=temperature,
                    max_tokens=max_tokens,
                )
                if out and out.strip():
                    if i != self._active:
                        print(
                            f"[Plan] now using {provider.name}:"
                            f"{getattr(provider, 'model', '?')}"
                        )
                        self._active = i
                    return out
                errors.append(f"{provider.name}: empty response")
                self._cooldown_until[i] = now + 60.0
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{provider.name}: {exc}")
                self._cooldown_until[i] = now + self._retry_after
                self._record_failover(provider.name)

        if not tried:
            # Everything is cooling down - clear the cooldowns and try the
            # last provider (usually the always-available local model).
            self._cooldown_until.clear()
            try:
                return self._providers[-1].generate(
                    prompt, system=system, temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{self._providers[-1].name}: {exc}")

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
