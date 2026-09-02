from __future__ import annotations

import concurrent.futures
import re
import time
from collections.abc import Callable

from src.ai.providers.base import (
    ChatProvider,
    ProviderError,
    VisionProvider,
)

# How long a rung sits out, by what went wrong with it.
#
# Everything used to get the same ten minutes, which meant one 503 -
# a blip, over in seconds - benched the best model for the length of
# several tasks. That is most of why Alfred spent last night planning
# with a 4B local model and doing visibly worse work.
_QUOTA = re.compile(r"429|RESOURCE_EXHAUSTED|quota|rate.?limit", re.I)
_REFUSED = re.compile(r"401|403|unauthor|forbidden|invalid.{0,10}key", re.I)
_BLIP = re.compile(
    r"50[0234]|timeout|timed out|unavailable|no answer in|"
    r"connection|reset|temporarily",
    re.I,
)


def _rest_for(exc: BaseException, default: float, patience: float) -> float:
    """How long to leave a rung alone after it failed."""
    text = str(exc)

    # Told outright when to come back.
    said = re.search(r"retry[ -]?after[\"':\s]+(\d+)", text, re.I)
    if said:
        return min(float(said.group(1)), 3600.0)

    if _REFUSED.search(text):
        return 3600.0        # not coming back this hour whatever we do
    if _QUOTA.search(text):
        return default       # out of allowance; wait it out
    if _BLIP.search(text):
        # Seconds, not minutes. A stumble is not an outage, and the
        # cost of asking again too soon is one wasted call.
        return max(30.0, patience * 2)
    return default


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
        patience: float = 12.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers = [p for p in providers if p is not None]
        if not self._providers:
            raise ProviderError("FallbackChatProvider needs at least one provider.")

        self._retry_after = retry_primary_after
        # How long a rung gets before the next one is tried. The
        # transport timeout underneath is three minutes, which is the
        # right patience for a request nobody is waiting on and quite
        # wrong for one somebody asked out loud: the bench watched a
        # four-second question take a hundred and twenty-three because
        # the good model was having a bad minute. This is not about
        # which model is better - it is about not being stuck with a
        # choice made before the call started.
        self._patience = patience
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
                out = self._ask(
                    provider, prompt, system, temperature, max_tokens,
                    # The last rung is the safety net. Hurrying it along
                    # would leave nowhere to fall.
                    patience=None if provider is self._providers[-1]
                    else self._patience,
                )
                if out and out.strip():
                    if i != self._active:
                        self._note_move(i, provider)
                        self._active = i
                    return out
                errors.append(f"{provider.name}: empty response")
                self._cooldown_until[i] = now + 60.0
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{provider.name}: {exc}")
                self._cooldown_until[i] = now + _rest_for(
                    exc, self._retry_after, self._patience
                )
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

    def _ask(self, provider, prompt, system, temperature, max_tokens,
             patience: float | None):
        """One rung's answer, or a timeout if it is taking too long.

        The call cannot be cancelled - it is a blocking HTTP request -
        so the wait is abandoned rather than the work. That thread
        finishes into nothing a moment later, which costs one wasted
        response and saves everybody the other two minutes.
        """
        call = lambda: provider.generate(          # noqa: E731
            prompt, system=system, temperature=temperature,
            max_tokens=max_tokens,
        )
        if patience is None:
            return call()

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(call)
            try:
                return future.result(timeout=patience)
            except concurrent.futures.TimeoutError:
                raise ProviderError(
                    f"no answer in {patience:.0f}s"
                ) from None
        finally:
            pool.shutdown(wait=False)

    @property
    def degraded(self) -> bool:
        """Is this running on something other than the best rung?

        Worth being able to ask. When the good models are out Alfred
        gets noticeably worse at planning, and until now that happened
        silently - the only clue was everything taking longer and going
        wrong more.
        """
        return self._active > 0

    def _note_move(self, i: int, provider) -> None:
        was = self._active
        self._active = i
        where = f"{provider.name}:{getattr(provider, 'model', '?')}"

        if i > was:
            print(f"[Plan] falling back to {where} - expect worse plans.")
        else:
            print(f"[Plan] back on {where}.")

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


def _is_useless(text: str) -> bool:
    """Did the model describe the screen, or echo the question?

    A weak vision model does not refuse a screenshot it cannot read - it
    fills in the template. qwen3.5:4b returned fifty-one lines of
    literal "<name> - center at (342, 587)". That is not a description,
    and passing it on as one is worse than failing, because it looks
    exactly like an answer.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]  # noqa: E741
    if not lines:
        return True

    echoed = sum(
        1 for l in lines  # noqa: E741
        if "<name>" in l.lower() or l.lower().startswith("<")
    )
    return echoed >= max(2, len(lines) // 2)


class FallbackVisionProvider(VisionProvider):
    """An ordered chain of vision providers, with the same cooldowns.

    Screenshots are the last resort for a window nothing else can read,
    so the model doing the reading has to be one that can actually see.
    A rung that fails is skipped until its cooldown expires.
    """

    def __init__(
        self,
        providers: list[VisionProvider],
        *,
        retry_primary_after: float = 600.0,
        patience: float = 12.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers = [p for p in providers if p is not None]
        if not self._providers:
            raise ProviderError(
                "FallbackVisionProvider needs at least one provider."
            )

        self._retry_after = retry_primary_after
        self._monotonic = monotonic
        self._cooldown_until: dict[int, float] = {}

        self.name = "chain(" + ">".join(
            f"{p.name}:{getattr(p, 'model', '?')}" for p in self._providers
        ) + ")"
        self.model = getattr(self._providers[0], "model", "")

    def analyze(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        mime_type: str = "image/png",
    ) -> str:
        now = self._monotonic()
        errors: list[str] = []

        for index, provider in enumerate(self._providers):
            if self._cooldown_until.get(index, 0.0) > now:
                continue
            try:
                out = provider.analyze(
                    image_bytes, prompt, mime_type=mime_type
                )
            except Exception as exc:  # noqa: BLE001
                self._cooldown_until[index] = now + self._retry_after
                errors.append(f"{provider.name}: {exc}")
                continue

            if out and out.strip() and not _is_useless(out):
                return out

            self._cooldown_until[index] = now + self._retry_after
            errors.append(
                f"{provider.name}: "
                + ("returned nothing" if not (out or "").strip()
                   else "cannot actually read images")
            )

        raise ProviderError(
            "no vision model could read the screenshot - "
            + "; ".join(errors[:3])
        )

    def unload(self) -> None:
        for provider in self._providers:
            try:
                provider.unload()
            except Exception:  # noqa: BLE001
                continue
