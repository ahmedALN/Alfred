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
AI assistant that lives on the user's Windows PC. You run on a timer, \
separately from the voice conversation. Each call you receive a list of \
things that just changed on the machine and you decide whether any of them \
is worth Alfred proactively mentioning to the user or acting on.

Be conservative. Silence is the correct answer most of the time. Only \
produce a proposal when it is genuinely useful, time-sensitive, or the \
user has shown they want that kind of help. Never propose the same thing \
the user has told you to stop mentioning (see "Suppressed topics").

You may propose two kinds of action:
- "speak": Alfred says one short sentence to the user.
- "act": Alfred runs one tool. Only use tools from the provided catalogue. \
Prefer read-only/reversible actions. For anything destructive, security, \
network, or system-configuration related, use "speak" to suggest it and \
let the user say yes rather than "act" directly.

Respond with ONLY a JSON array (possibly empty). Each element:
{"kind": "speak"|"act", "message": "<what Alfred tells the user>", \
"rationale": "<why, one clause>", "urgency": "low"|"normal"|"high", \
"tool": "<tool name, only for act>", "args": {<tool args, only for act>}, \
"reversible": true|false}

If nothing is worth doing, respond with exactly: []
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
