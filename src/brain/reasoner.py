from __future__ import annotations

import json
from typing import Protocol

from src.ai.providers.base import ChatProvider
from src.brain.types import DeliberationContext, Proposal


class Reasoner(Protocol):
    """
    The swappable "brain model". Given everything perceived this tick,
    decide what (if anything) Alfred should say or do.

    Implementations must be synchronous and side-effect free: the
    orchestrator runs them in a worker thread and owns all execution.
    """

    def decide(self, context: DeliberationContext) -> list[Proposal]:
        ...


SYSTEM_PREAMBLE = """You are the background awareness process for Alfred, an \
AI assistant on the user's Windows PC. You run on a timer. Each call you \
get a short list of things that just changed on the machine. Your job: for \
each change a thoughtful human assistant would speak up about, produce a \
proposal.

Speak up when a change is useful to know, needs attention, or a small \
helpful action would fix it: low disk space, a firewall turned off, a \
process pegging the CPU, a reboot pending, the battery getting low, an \
unexpected new listening port, the machine up for weeks. Phrase "speak" \
messages as one natural spoken sentence, the way Alfred would say it out \
loud.

Stay silent (return []) only for genuinely trivial noise, or a topic under \
"Suppressed topics".

Two proposal kinds:
- "speak": Alfred says one short sentence to the user.
- "act": Alfred runs ONE tool from the catalogue. Read-only checks \
(system_info, network_info) are fine to run directly. For anything that \
changes the system, use "speak" to suggest it instead.

Only ever refer to a change that is explicitly in the list below. Never \
invent a change, and never copy an example - if the list is empty or \
trivial, return [].

Respond with ONLY a JSON array. Each element:
{"kind":"speak"|"act","message":"<one spoken sentence about a listed \
change>","rationale":"<why, short>","urgency":"low"|"normal"|"high",\
"tool":"<name, act only>","args":{<args, act only>}}

If the change is "[info] CPU load: 4%" or similar routine noise, the \
answer is [].
"""


def build_prompt(context: DeliberationContext) -> str:
    notable_lines = "\n".join(
        f"- [{n.severity}] ({n.source}) {n.summary}"
        for n in context.notables
    ) or "- (none)"

    tool_lines = "\n".join(
        f"- {tool.get('name')}: {tool.get('description', '')}"
        for tool in context.tool_catalogue
    ) or "- (none)"

    suppressed = "\n".join(
        f"- {item}" for item in context.suppressions
    ) or "- (none)"

    recent = "\n".join(
        f"{turn.get('role', '?')}: {turn.get('text', '')}"
        for turn in context.recent_turns[-8:]
    ) or "(no recent conversation)"

    memory = context.memory_context.strip() or "(nothing remembered yet)"

    return (
        f"{SYSTEM_PREAMBLE}\n"
        f"Autonomy mode: {context.autonomy}\n\n"
        f"What just changed:\n{notable_lines}\n\n"
        f"Tools Alfred can run:\n{tool_lines}\n\n"
        f"Suppressed topics (never raise these):\n{suppressed}\n\n"
        f"What Alfred remembers about this user/machine:\n{memory}\n\n"
        f"Recent conversation:\n{recent}\n\n"
        f"Your JSON array:"
    )


def parse_proposals(raw_text: str) -> list[Proposal]:
    """Tolerant parser mirroring MemoryLearner._parse_facts."""

    cleaned = raw_text.strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]

    cleaned = cleaned.strip()

    # Salvage the outermost array if the model wrapped it in prose.
    if not cleaned.startswith("["):
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return []

    if not isinstance(parsed, list):
        return []

    proposals: list[Proposal] = []

    for item in parsed:
        if not isinstance(item, dict):
            continue

        proposal = Proposal.from_dict(item)

        if proposal is not None:
            proposals.append(proposal)

    return proposals


class LLMReasoner:
    """
    Default reasoner: whatever ChatProvider Alfred is configured with
    (Gemini, Ollama, or an OpenAI-compatible endpoint).
    """

    def __init__(self, chat: ChatProvider) -> None:
        self._chat = chat

    def decide(self, context: DeliberationContext) -> list[Proposal]:
        prompt = build_prompt(context)

        try:
            raw_text = self._chat.generate(prompt, temperature=0.3)
        except Exception as exc:  # noqa: BLE001 - brain must survive
            print(f"[Brain/Reasoner] generation failed: {exc}")
            return []

        if not raw_text:
            return []

        return parse_proposals(raw_text)


# Backwards-compatible alias.
GeminiReasoner = LLMReasoner
