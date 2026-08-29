from __future__ import annotations

from typing import Any

from src.brain.reasoner import Reasoner
from src.brain.types import DeliberationContext, Notable, Proposal
from src.memory.learner import MemoryLearner
from src.memory.store import MemoryStore

# Facts stored with this prefix mean "the user told Alfred to stop
# proactively raising this topic". They are surfaced to the reasoner as
# hard suppressions and also checked by policy.
SUPPRESS_PREFIX = "SUPPRESS:"


def collect_suppressions(store: MemoryStore) -> list[str]:
    out: list[str] = []

    for fact in store.all_facts():
        content = fact.content.strip()

        if content.upper().startswith(SUPPRESS_PREFIX):
            topic = content[len(SUPPRESS_PREFIX):].strip()

            if topic:
                out.append(topic)

    return out


class Deliberator:
    """
    Turns a batch of notables into concrete proposals by assembling
    context and handing it to the swappable reasoner.
    """

    def __init__(
        self,
        reasoner: Reasoner,
        store: MemoryStore,
        learner: MemoryLearner,
        tool_catalogue: list[dict[str, Any]],
        autonomy: str,
        recent_turns_limit: int = 8,
    ) -> None:
        self._reasoner = reasoner
        self._store = store
        self._learner = learner
        self._tool_catalogue = tool_catalogue
        self._autonomy = autonomy
        self._recent_turns_limit = recent_turns_limit

    def deliberate(
        self,
        notables: list[Notable],
        session_id: str | None,
    ) -> list[Proposal]:
        if not notables:
            return []

        try:
            memory_context = self._learner.recall_context()
        except Exception as exc:  # noqa: BLE001
            print(f"[Brain/Deliberation] recall_context failed: {exc}")
            memory_context = ""

        recent_turns: list[dict[str, str]] = []

        if session_id:
            try:
                recent_turns = self._store.session_turns(session_id)[
                    -self._recent_turns_limit:
                ]
            except Exception as exc:  # noqa: BLE001
                print(f"[Brain/Deliberation] session_turns failed: {exc}")

        context = DeliberationContext(
            notables=notables,
            memory_context=memory_context,
            recent_turns=recent_turns,
            tool_catalogue=self._tool_catalogue,
            suppressions=collect_suppressions(self._store),
            autonomy=self._autonomy,
        )

        proposals = self._reasoner.decide(context)

        # Final guard: drop any proposal that names a suppressed topic
        # even if the reasoner ignored the instruction.
        suppressed = [s.lower() for s in context.suppressions]

        kept: list[Proposal] = []

        for proposal in proposals:
            haystack = f"{proposal.message} {proposal.rationale}".lower()

            if any(topic in haystack for topic in suppressed if topic):
                print(
                    "[Brain/Deliberation] dropped proposal on suppressed "
                    f"topic: {proposal.message!r}"
                )
                continue

            kept.append(proposal)

        return kept
