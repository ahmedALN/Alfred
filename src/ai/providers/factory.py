from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.ai.providers.base import (
    ChatProvider,
    EmbeddingProvider,
    ProviderError,
    VisionProvider,
)
from src.ai.providers.gemini_provider import (
    GeminiChatProvider,
    GeminiEmbeddingProvider,
    GeminiVisionProvider,
)
from src.ai.providers.ollama_provider import (
    OllamaChatProvider,
    OllamaEmbeddingProvider,
    OllamaVisionProvider,
)
from src.ai.providers.openai_provider import (
    OpenAICompatibleChatProvider,
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleVisionProvider,
)

SUPPORTED = ("gemini", "ollama", "openai")

# Per-capability fallback models used when the operator does not set an
# explicit *_MODEL. Keys are (provider, capability).
_DEFAULT_MODELS: dict[tuple[str, str], str] = {
    ("gemini", "embed"): "gemini-embedding-001",
    ("gemini", "vision"): "gemini-flash-lite-latest",
    ("ollama", "chat"): "qwen3.5",
    ("ollama", "embed"): "nomic-embed-text",
    ("ollama", "vision"): "moondream",
    # The strong planner, now a fallback rather than the primary.
    ("openai", "plan"): "nvidia/nemotron-3-super-120b-a12b",
}


@dataclass(frozen=True)
class ProviderBundle:
    chat: ChatProvider
    embedder: EmbeddingProvider
    vision: VisionProvider
    plan_chat: ChatProvider
    # Small, well-specified jobs: reading an answer out of tool output,
    # writing a one-line lesson. They do not need the planner, and going
    # through it made a learned routine - one tool call, no thinking -
    # take eleven seconds, ten of them waiting for a 120B model to
    # summarise a line of PowerShell output.
    fast_chat: ChatProvider

    def describe(self) -> str:
        return (
            f"chat={self.chat.name}:{self.chat.model or '?'} "
            f"embed={self.embedder.name}:{self.embedder.model or '?'} "
            f"vision={self.vision.name}:{self.vision.model or '?'} "
            f"plan={self.plan_chat.name} "
            f"fast={self.fast_chat.name}"
        )


def _resolve(name: str | None, fallback: str) -> str:
    chosen = (name or fallback or "gemini").strip().lower()

    if chosen not in SUPPORTED:
        raise ProviderError(
            f"Unknown AI provider {chosen!r}. Supported: {', '.join(SUPPORTED)}."
        )

    return chosen


def _model_for(
    provider: str,
    capability: str,
    explicit: str | None,
    gemini_chat_default: str,
) -> str:
    if explicit:
        return explicit

    if provider == "gemini" and capability == "chat":
        return gemini_chat_default

    return _DEFAULT_MODELS.get((provider, capability), "")


def build_providers(settings: Any, gemini_client: Any) -> ProviderBundle:
    """
    Construct the chat / embedding / vision providers from settings.

    ``ALFRED_AI_PROVIDER`` picks the default backend for all three;
    ``ALFRED_AI_CHAT_PROVIDER`` / ``_EMBED_PROVIDER`` / ``_VISION_PROVIDER``
    override individual capabilities. Voice always stays on Gemini Live
    and is not routed through here.
    """

    default = _resolve(settings.ai_provider, "gemini")

    chat_provider = _resolve(settings.ai_chat_provider, default)
    embed_provider = _resolve(settings.ai_embed_provider, default)
    vision_provider = _resolve(settings.ai_vision_provider, default)

    gemini_chat_default = settings.gemini_text_model

    chat = _build_chat(
        chat_provider,
        _model_for(
            chat_provider, "chat", settings.ai_chat_model, gemini_chat_default
        ),
        settings,
        gemini_client,
    )

    embedder = _build_embed(
        embed_provider,
        _model_for(
            embed_provider, "embed", settings.ai_embed_model, gemini_chat_default
        ),
        settings,
        gemini_client,
    )

    vision = _build_vision(
        vision_provider,
        _model_for(
            vision_provider,
            "vision",
            settings.ai_vision_model,
            gemini_chat_default,
        ),
        settings,
        gemini_client,
    )

    return ProviderBundle(
        chat=chat, embedder=embedder, vision=vision,
        plan_chat=build_plan_chat(settings, gemini_client, chat),
        fast_chat=build_fast_chat(settings, gemini_client, chat),
    )


def build_fast_chat(
    settings: Any, gemini_client: Any, local_chat: ChatProvider
) -> ChatProvider:
    """The quick lane: small prompts, short answers, no judgement needed.

    Measured on this machine: flash-lite answers a planner-sized prompt
    in 0.6-0.9 seconds where the big planner takes 1.9-14.4, and for
    "turn this PowerShell output into a sentence" the difference is the
    whole cost of the job.

    Falls back to the local model, which is slower than flash-lite but
    cannot run out of quota or go down.

    That local model is the 4B on purpose, and it is worth writing down
    why, because a 9B is sitting on this machine and looks like an
    obvious upgrade. Measured on the executor's real prompt: the 4B
    chose the right tool 3 times out of 3 in 5.2s; the 9B managed 2 out
    of 3 in 8.4s, went exploratory where the 4B went straight to the
    work, and once emitted JSON in the wrong shape. Bigger is not better
    at emitting a strict format on 8GB of VRAM. Do not swap it.
    """
    from src.ai.providers.fallback import FallbackChatProvider

    chain: list[ChatProvider] = []
    try:
        chain.append(
            GeminiChatProvider(gemini_client, "gemini-flash-lite-latest")
        )
    except Exception:  # noqa: BLE001
        pass
    chain.append(local_chat)

    if len(chain) == 1:
        return chain[0]
    return FallbackChatProvider(chain, patience=8.0)


def build_plan_chat(
    settings: Any, gemini_client: Any, local_chat: ChatProvider
) -> ChatProvider:
    """
    The chat provider used for task PLANNING: the configured plan
    provider (NVIDIA Nemotron by default) with an automatic fallback
    chain to the listed backends, ending at the fast local model.
    """

    from src.ai.providers.fallback import FallbackChatProvider

    chain: list[ChatProvider] = []

    primary = _resolve(settings.ai_plan_provider, "openai")
    have_primary = not (primary == "openai" and not settings.openai_api_key)

    if have_primary:
        try:
            chain.append(
                _build_chat(
                    primary, settings.ai_plan_model, settings, gemini_client
                )
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[Plan] primary planner unavailable ({exc}); using fallbacks.")
    else:
        print(
            "[Plan] no ALFRED_OPENAI_API_KEY set - planning uses "
            f"{settings.ai_plan_fallbacks[0] if settings.ai_plan_fallbacks else 'local'}."
        )

    for name in settings.ai_plan_fallbacks:
        try:
            if name == "gemini":
                chain.append(
                    GeminiChatProvider(gemini_client, settings.gemini_text_model)
                )
                # A lighter, less-contended rung: flash-latest often 503s
                # under load while flash-lite stays up.
                lite = "gemini-flash-lite-latest"
                if settings.gemini_text_model != lite:
                    chain.append(GeminiChatProvider(gemini_client, lite))
            elif name == "openai":
                # Only ever handled as the PRIMARY before, so listing it
                # as a fallback silently dropped it - which is how the
                # big model vanished from the chain entirely the moment
                # the fast one was promoted ahead of it.
                if settings.openai_api_key and primary != "openai":
                    chain.append(
                        _build_chat(
                            "openai",
                            _DEFAULT_MODELS[("openai", "plan")],
                            settings, gemini_client,
                        )
                    )
            elif name == "ollama":
                model = settings.ai_chat_model or _DEFAULT_MODELS[("ollama", "chat")]
                chain.append(OllamaChatProvider(model, settings.ollama_base_url))
        except Exception:  # noqa: BLE001
            pass

    # Nothing twice: promoting one rung above another must not leave the
    # loser in the list a second time under a different name.
    seen: set[str] = set()
    deduped = []
    for provider in chain:
        key = f"{provider.name}:{getattr(provider, 'model', '')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(provider)
    chain = deduped

    if not chain:
        chain.append(local_chat)

    # Always keep the local model as the final safety net.
    if not any(getattr(p, "name", "") == "ollama" for p in chain):
        chain.append(local_chat)

    import os

    # Optional: a quick reachability check that reorders the chain so the
    # planner Alfred lands on first is one that actually works right now.
    # Off by default - it costs a few seconds and a token at startup, and
    # FallbackChatProvider's per-rung cooldown already routes around a
    # dead provider after the first call.
    if os.getenv("ALFRED_PLAN_PROBE", "false").strip().lower() in (
        "1", "true", "yes", "on"
    ):
        chain = _probe_and_order(chain)

    return FallbackChatProvider(chain) if len(chain) > 1 else chain[0]


def _probe_and_order(chain: list[ChatProvider]) -> list[ChatProvider]:
    """A quick reachability check at startup so Alfred knows which planner
    it can actually use right now (NVIDIA entitlement, Gemini quota and
    the local server all vary). Reachable providers keep their configured
    order and move ahead of unreachable ones. Never blocks startup for
    more than ~12s."""
    import concurrent.futures as _cf

    if len(chain) < 2:
        return chain

    def _ok(p: ChatProvider) -> bool:
        try:
            out = p.generate("Reply with the single word: ok", max_tokens=8)
            return bool(out and out.strip())
        except Exception:  # noqa: BLE001
            return False

    results: dict[int, bool] = {}
    try:
        with _cf.ThreadPoolExecutor(max_workers=len(chain)) as ex:
            futs = {ex.submit(_ok, p): i for i, p in enumerate(chain)}
            for fut in _cf.as_completed(futs, timeout=9):
                results[futs[fut]] = bool(fut.result())
    except Exception:  # noqa: BLE001 - timeout or worse: keep what we have
        pass

    live = [p for i, p in enumerate(chain) if results.get(i)]
    dead = [p for i, p in enumerate(chain) if not results.get(i)]

    for p in live:
        print(f"[Plan] reachable now: {p.name}:{getattr(p, 'model', '?')}")
    for p in dead:
        print(f"[Plan] not reachable now: {p.name}:{getattr(p, 'model', '?')}")

    return (live + dead) or chain


def _build_chat(
    provider: str, model: str, settings: Any, gemini_client: Any
) -> ChatProvider:
    if provider == "gemini":
        return GeminiChatProvider(gemini_client, model)

    if provider == "ollama":
        return OllamaChatProvider(model, settings.ollama_base_url)

    return OpenAICompatibleChatProvider(
        model, settings.openai_base_url or "", settings.openai_api_key
    )


def _build_embed(
    provider: str, model: str, settings: Any, gemini_client: Any
) -> EmbeddingProvider:
    if provider == "gemini":
        return GeminiEmbeddingProvider(gemini_client, model)

    if provider == "ollama":
        return OllamaEmbeddingProvider(model, settings.ollama_base_url)

    return OpenAICompatibleEmbeddingProvider(
        model, settings.openai_base_url or "", settings.openai_api_key
    )


def _ollama_can_see(model: str, base_url: str) -> bool:
    """Does this local model claim to accept images?

    Only a claim: Ollama reports qwen3.5:4b as vision-capable and it
    still answers a screenshot with fifty lines of literal "<name>".
    What a model says it can do and what it does are different
    questions, and only the second one matters - see _is_useless.
    """
    try:
        import httpx

        response = httpx.post(
            f"{base_url.rstrip('/')}/api/show",
            json={"name": model},
            timeout=6.0,
        )
        response.raise_for_status()
        info = response.json()
    except Exception:  # noqa: BLE001
        return False

    families = (info.get("details") or {}).get("families") or []
    families = [str(f).lower() for f in families]

    # Vision models carry an image encoder alongside the language model.
    if any(f in ("clip", "mllama", "vision", "siglip") for f in families):
        return True

    return "vision" in str(info.get("capabilities", "")).lower() or (
        "vision" in [str(c).lower() for c in (info.get("capabilities") or [])]
    )


def _one_vision(
    provider: str, model: str, settings: Any, gemini_client: Any
) -> VisionProvider:
    if provider == "gemini":
        return GeminiVisionProvider(gemini_client, model)

    if provider == "ollama":
        return OllamaVisionProvider(model, settings.ollama_base_url)

    return OpenAICompatibleVisionProvider(
        model, settings.openai_base_url or "", settings.openai_api_key
    )


def _build_vision(
    provider: str, model: str, settings: Any, gemini_client: Any
) -> VisionProvider:
    """Prefer a model that can actually see, and chain the rest behind it.

    Screenshots are what Alfred falls back to when nothing else can read
    a window - a game, a launcher drawing its own buttons. Getting a
    confident description from a model that cannot see is worse than
    getting nothing, because it looks exactly like an answer.
    """
    from src.ai.providers.fallback import FallbackVisionProvider

    chain: list[VisionProvider] = [
        _one_vision(provider, model, settings, gemini_client)
    ]

    # A capable rung behind the configured one. The chain notices a model
    # that fills in the template instead of reading the screen, and puts
    # it on cooldown - so a local model that cannot really see costs one
    # call, not every call.
    if provider != "gemini" and getattr(settings, "gemini_api_key", ""):
        chain.append(
            GeminiVisionProvider(
                gemini_client, _DEFAULT_MODELS[("gemini", "vision")]
            )
        )

    if len(chain) == 1:
        return chain[0]

    return FallbackVisionProvider(chain)
