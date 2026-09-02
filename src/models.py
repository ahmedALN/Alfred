"""
python -m src.models  -  which model answers, and how fast.

Alfred's speed is mostly not Alfred. It is which rung of the planning
chain answers, and how long that one takes today - so "is this setup
any good" is a question about models, and it changes every time a free
tier is spent or a provider has a bad afternoon.

    python -m src.models              time the chain Alfred is using
    python -m src.models try <base_url> <key> <model>
                                      time a provider you are thinking
                                      about, before wiring it in

Both send the same planner-sized prompt Alfred really sends - a tool
catalogue, an environment block and a goal - because a model that
answers "say ready" in half a second may take thirty on the real thing.
gemini-flash-lite did exactly that here: 1.4s at the median and 31.8s
on the first call after a pause.

Nothing here is written down and nothing is changed. It prints numbers.
"""

from __future__ import annotations

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


def main(argv: list[str]) -> int:
    if argv and argv[0] == "try":
        if len(argv) < 4:
            print("usage: python -m src.models try <base_url> <key> <model>")
            return 2
        return time_a_candidate(argv[1], argv[2], argv[3])

    if argv:
        print(__doc__)
        return 2

    return time_the_chain()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
