"""
python -m src.models  -  which model answers, and how fast.

Alfred's speed is mostly not Alfred. It is which rung of the planning
chain answers, and how long that one takes today - so "is this setup
any good" is a question about models, and it changes every time a free
tier is spent or a provider has a bad afternoon.

    python -m src.models              time the chain Alfred is using
    python -m src.models add groq <key>
                                      measure a provider's models, keep
                                      the best, and wire it in
    python -m src.models try <base_url> <key> <model>
                                      time one model, change nothing

All of them send the same planner-sized prompt Alfred really sends - a
tool catalogue, an environment block and a goal - because a model that
answers "say ready" in half a second may take thirty on the real thing.
gemini-flash-lite did exactly that here: 1.4s at the median and 31.8s
on the first call after a pause.

`add` knows the endpoints for groq, cerebras, openrouter, together,
mistral and nvidia, and takes a base URL for anything else. It asks the
provider what models it serves rather than trusting a name written down
months ago, times the plausible ones, and keeps the fastest that
produces an actual plan - a model that answers instantly by echoing the
template back is not a fast model, and one of NVIDIA's does exactly
that. It writes ALFRED_OPENAI<N>_* into .env and puts the new rung in
the chain; a key that does not work changes nothing.

`add` is the only one that writes anything. The rest print numbers.
"""

from __future__ import annotations

import re
import sys
import time

# The shape and size of a real planning prompt. Roughly 1,800 tokens,
# which is what Alfred's planner actually carries.
PROMPT = (
    "You plan Windows tasks. TOOLS:\n"
    + "\n".join(
        f"- tool_{i}(action, window, name, text, query, item, path, keys, "
        f"ref, timeout): does thing {i} on Windows, with caveats about "
        f"when it will and will not work."
        for i in range(28)
    )
    + "\n\nENVIRONMENT:\n"
    + "\n".join(f"C:/Users/me/folder_{i} exists" for i in range(30))
    + "\n\nGOAL: In the alfred-bench folder on my Desktop, make a "
      "subfolder called text and move the .txt files into it.\n"
      'Reply with JSON only: a list of at most 5 steps, each '
      '{"step": "...", "done_when": "..."}.'
)

RUNS = 3


def _time(provider, runs: int = RUNS) -> tuple[list[float], str, str]:
    """Seconds per run, a sample of the answer, and any error."""
    times: list[float] = []
    sample = ""

    for _ in range(runs):
        started = time.time()
        try:
            out = provider.generate(
                PROMPT, max_tokens=700, temperature=0.2,
                system="detailed thinking off",
            )
        except Exception as exc:  # noqa: BLE001
            return times, sample, str(exc)[:90]

        times.append(time.time() - started)
        sample = " ".join(str(out).split())[:60]

    return times, sample, ""


def _looks_like_a_plan(sample: str) -> bool:
    """Fast and useless is not fast.

    nemotron-3.5-lightning answered in 8.6s with the literal template
    it had been shown - {"step": "...", "done_when": "..."} - which is
    a very quick way of saying nothing.
    """
    lowered = sample.lower()

    if '"..."' in sample or "step\": \"...\"" in sample:
        return False

    return "step" in lowered and len(sample) > 30


def _report(label: str, times: list[float], sample: str, error: str) -> None:
    if error:
        print(f"  {label:46} FAILED  {error}")
        return

    ordered = sorted(times)
    median = ordered[len(ordered) // 2]
    verdict = "" if _looks_like_a_plan(sample) else "   <- not a real plan"

    print(
        f"  {label:46} "
        + " ".join(f"{t:6.2f}s" for t in times)
        + f"   median {median:5.2f}s{verdict}"
    )

    if verdict:
        print(f"  {'':46} got: {sample}")


def time_the_chain() -> int:
    from google import genai

    from src.ai.providers.factory import build_providers
    from src.config import load_settings

    settings = load_settings()
    bundle = build_providers(
        settings, genai.Client(api_key=settings.gemini_api_key)
    )

    print("Alfred's planning chain, in preference order")
    print("=" * 88)
    print(f"  {'model':46} {'runs':^24}")
    print()

    chain = bundle.plan_chat
    rungs = list(getattr(chain, "_providers", [chain]))

    for provider in rungs:
        label = f"{provider.name}:{getattr(provider, 'model', '?')}"
        _report(label, *_time(provider))

    print()
    print("  The first rung that answers wins, and a rung that is merely")
    print("  slow is raced rather than dropped. A median above about 8s")
    print("  here is what a 40-second task feels like, because a task is")
    print("  a plan, a step or two, and a check.")

    return 0


def time_a_candidate(base_url: str, key: str, model: str) -> int:
    from src.ai.providers.openai_provider import OpenAICompatibleChatProvider

    provider = OpenAICompatibleChatProvider(
        model=model, base_url=base_url, api_key=key,
    )

    print(f"{model} at {base_url}")
    print("=" * 88)
    _report(model, *_time(provider))
    print()
    print("  If that median beats the rungs in `python -m src.models`,")
    print("  add it as ALFRED_OPENAI2_* and put openai2 first in")
    print("  ALFRED_AI_PLAN_FALLBACKS.")

    return 0


# ====================================================================
# Adding a provider
#
# The endpoints are stable and public; which models each one serves is
# not, and neither is which of them is any good at planning. So this
# asks the provider what it has, times the plausible ones on the real
# prompt, and keeps the fastest that produces an actual plan - rather
# than trusting a model name written down months ago.
# ====================================================================

KNOWN_PROVIDERS: dict[str, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "together": "https://api.together.xyz/v1",
    "mistral": "https://api.mistral.ai/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
}

# Models that are not for this. Planning wants an instruction-following
# chat model; everything here is a different job wearing the same API.
_NOT_A_PLANNER = re.compile(
    r"whisper|tts|audio|speech|embed|rerank|moderat|guard|safety|"
    r"vision|image|diffus|video|code(?!stral)|prompt-guard|"
    r"nemoguard|parse|reward",
    re.I,
)

# Preferred when choosing what to time first: small and instant beats
# large and thorough for a planner, and a name saying so is a good clue.
_LOOKS_QUICK = re.compile(r"instant|fast|lite|mini|flash|8b|7b|9b|small", re.I)


def _list_models(base_url: str, key: str) -> list[str]:
    import json
    import urllib.request

    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {key}"},
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())

    return sorted(
        str(entry.get("id", ""))
        for entry in payload.get("data", [])
        if entry.get("id")
    )


def _worth_timing(models: list[str], limit: int = 6) -> list[str]:
    """The handful worth spending a measurement on."""
    usable = [m for m in models if not _NOT_A_PLANNER.search(m)]

    # Quick-sounding ones first, then the rest, because a planner that
    # answers in under a second is the whole point of adding one.
    quick = [m for m in usable if _LOOKS_QUICK.search(m)]
    rest = [m for m in usable if m not in quick]

    return (quick + rest)[:limit]


def _slot_for(name: str) -> str:
    """Which ALFRED_OPENAI<N>_* slot to use.

    The first free one, so adding a second provider does not quietly
    overwrite the first.
    """
    import os

    from dotenv import dotenv_values

    existing = dotenv_values(".env")

    for number in range(2, 10):
        slot = f"OPENAI{number}"

        if not (existing.get(f"ALFRED_{slot}_API_KEY") or "").strip():
            return slot.lower()

        # Already ours: reuse it rather than piling up duplicates.
        if (existing.get(f"ALFRED_{slot}_BASE_URL") or "") == KNOWN_PROVIDERS.get(
            name, ""
        ):
            return slot.lower()

    assert os  # keeps the import honest if the loop is ever changed
    return "openai9"


def _write_env(values: dict[str, str], fallbacks: str) -> None:
    """Update .env in place, leaving everything else exactly as it was."""
    import pathlib

    path = pathlib.Path(".env")
    lines = path.read_text(encoding="utf-8").splitlines()
    wanted = dict(values)
    wanted["ALFRED_AI_PLAN_FALLBACKS"] = fallbacks

    out: list[str] = []
    seen: set[str] = set()

    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""

        if key in wanted:
            out.append(f"{key}={wanted[key]}")
            seen.add(key)
        else:
            out.append(line)

    missing = [k for k in wanted if k not in seen]

    if missing:
        out.append("")
        out.append("# Added by: python -m src.models add")
        out.extend(f"{k}={wanted[k]}" for k in missing)

    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def add_provider(name: str, key: str, base_url: str = "") -> int:
    """Measure a provider's models, keep the best, and wire it in."""
    from src.ai.providers.openai_provider import OpenAICompatibleChatProvider

    name = name.strip().lower()
    base = (base_url or KNOWN_PROVIDERS.get(name, "")).strip()

    if not base:
        print(f"no endpoint known for {name!r}. Either use one of:")
        print("   " + ", ".join(sorted(KNOWN_PROVIDERS)))
        print("or give the base URL:")
        print(f"   python -m src.models add {name} <key> <base_url>")
        return 2

    print(f"{name} at {base}")
    print("=" * 88)

    try:
        models = _list_models(base, key)
    except Exception as exc:  # noqa: BLE001
        print(f"  could not list models: {str(exc)[:120]}")
        print("  (a 401 here means the key is wrong; nothing was written)")
        return 1

    shortlist = _worth_timing(models)

    if not shortlist:
        print(f"  {len(models)} models listed, none of them a planner")
        return 1

    print(f"  {len(models)} models listed; timing {len(shortlist)} of them\n")

    results: list[tuple[float, str, str]] = []

    for model in shortlist:
        provider = OpenAICompatibleChatProvider(
            model=model, base_url=base, api_key=key,
        )
        times, sample, error = _time(provider, runs=2)
        _report(model, times, sample, error)

        if not error and _looks_like_a_plan(sample):
            results.append((sorted(times)[len(times) // 2], model, sample))

    if not results:
        print("\n  nothing here produced a usable plan; nothing written")
        return 1

    results.sort()
    best_time, best_model, _sample = results[0]

    slot = _slot_for(name)
    print(f"\n  best: {best_model} at {best_time:.2f}s   -> ALFRED_{slot.upper()}_*")

    # Where it goes in the chain. Faster than what is already there and
    # it goes first; otherwise it slots in behind the fast rung and
    # ahead of the slow ones.
    from src.config import load_settings

    settings = load_settings()
    current = [f for f in settings.ai_plan_fallbacks if f != slot]

    if slot in current:
        current.remove(slot)

    # Always ahead of "openai" (the big slow one) and "ollama" (the net).
    where = 0 if best_time < 3.0 else max(
        0, min(
            [i for i, f in enumerate(current) if f in ("openai", "ollama")]
            or [len(current)]
        )
    )
    current.insert(where, slot)

    _write_env(
        {
            f"ALFRED_{slot.upper()}_BASE_URL": base,
            f"ALFRED_{slot.upper()}_API_KEY": key,
            f"ALFRED_{slot.upper()}_MODEL": best_model,
        },
        ",".join(current),
    )

    print(f"  written to .env; plan chain is now: {','.join(current)}")
    print("\n  Restart Alfred to pick it up, then check with:")
    print("      python -m src.models")

    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "try":
        if len(argv) < 4:
            print("usage: python -m src.models try <base_url> <key> <model>")
            return 2
        return time_a_candidate(argv[1], argv[2], argv[3])

    if argv and argv[0] == "add":
        if len(argv) < 3:
            print("usage: python -m src.models add <groq|cerebras|openrouter> <key>")
            print("       python -m src.models add <name> <key> <base_url>")
            return 2
        return add_provider(argv[1], argv[2], argv[3] if len(argv) > 3 else "")

    if argv:
        print(__doc__)
        return 2

    return time_the_chain()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
