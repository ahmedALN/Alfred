from __future__ import annotations

from typing import Any

from src.brain.reasoner import Reasoner
from src.brain.types import (
    DeliberationContext,
    Notable,
    Proposal,
    ProposalKind,
)
from src.memory.learner import MemoryLearner
from src.memory.store import MemoryStore

# Facts stored with this prefix mean "the user told Alfred to stop
# proactively raising this topic". They are surfaced to the reasoner as
# hard suppressions and also checked by policy.
SUPPRESS_PREFIX = "SUPPRESS:"


_STOPWORDS = {
    "the", "a", "an", "is", "of", "on", "at", "and", "to", "in", "was",
    "now", "has", "your", "you", "it", "for", "with", "that", "this",
}


def _keywords(text: str) -> set[str]:
    return {
        w.strip(".,:;()%")
        for w in text.lower().split()
        if len(w) > 3 and w not in _STOPWORDS
    }


def _shares_keywords(a: str, b: str) -> bool:
    ka, kb = _keywords(a), _keywords(b)
    return bool(ka and kb and (ka & kb))


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

        # Safety net: a small local model often just returns []. Make
        # sure anything the perception layer flagged as warn/critical is
        # still voiced, unless the user suppressed that topic or the
        # reasoner already covered it.
        spoken_text = " ".join(
            f"{p.message} {p.rationale}" for p in kept
        ).lower()

        for notable in notables:
            if notable.severity not in ("warn", "critical"):
                continue

            summary_l = notable.summary.lower()

            if any(topic in summary_l for topic in suppressed if topic):
                continue

            key_hint = notable.key.split(".")[0].lower()
            if key_hint and key_hint in spoken_text:
                continue
            if _shares_keywords(summary_l, spoken_text):
                continue

            kept.append(
                Proposal(
                    kind=ProposalKind.SPEAK,
                    message=notable.summary,
                    rationale="flagged by perception; reasoner was silent",
                    urgency="high" if notable.severity == "critical" else "normal",
                )
            )

        return kept
