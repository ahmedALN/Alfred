from __future__ import annotations

import json
from typing import Any

from src.ai.providers.base import ChatProvider, EmbeddingProvider
from src.memory.embeddings import cosine_similarity
from src.memory.store import Fact, MemoryStore


# Above this similarity to an existing fact, treat a new statement as
# reinforcing that fact rather than creating a duplicate.
DEDUPE_THRESHOLD = 0.90

# Minimum similarity for a stored fact to be considered "relevant"
# enough to surface into the live system prompt.
RECALL_THRESHOLD = 0.55


DISTILLATION_PROMPT = """You are Alfred's memory distillation process.
Read the conversation transcript below between Alfred (an AI desktop
assistant) and its user, running on the user's own PC.

Extract only durable, reusable facts worth remembering for future
sessions: stable user preferences, facts about the user's machine or
software setup, recurring habits, names of things (apps, projects,
devices), and corrections the user gave Alfred about how to behave.

Do NOT extract: one-off task requests, small talk, or anything only
relevant to this single conversation.

Respond with ONLY a JSON array of objects, each shaped like:
{{"content": "<one self-contained factual sentence>", "category": "<preference|system|habit|correction|general>", "confidence": <0.0-1.0>}}

If there is nothing worth remembering, respond with exactly: []

Transcript:
{transcript}
"""


class MemoryLearner:
    """
    Bridges the live Gemini session and MemoryStore.

    - `remember()` is called by the AI mid-conversation (via the
      `remember` tool) or by passive distillation after a session ends.
    - `recall_context()` builds the "what Alfred already knows"
      block injected into the system prompt at connect time.
    - `distill_session()` runs once per session close and turns the
      raw transcript into new durable facts automatically, so Alfred
      keeps getting more useful the more it's used.
    """

    def __init__(
        self,
        store: MemoryStore,
        chat: ChatProvider | None = None,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self._store = store
        self._chat = chat
        self._embedder = embedder

    # ----------------------------------------------------------------
    # Active learning (explicit)
    # ----------------------------------------------------------------

    def remember(
        self,
        content: str,
        category: str = "general",
        confidence: float = 0.8,
        source: str = "conversation",
    ) -> dict[str, Any]:
        content = content.strip()

        if not content:
            return {"status": "error", "error": "Empty fact content."}

        embedding = self._embed(content)

        existing = self._find_similar(content, embedding)

        if existing is not None:
            fact, similarity = existing

            self._store.reinforce_fact(
                fact.id,
                confidence=max(fact.confidence, confidence),
            )

            return {
                "status": "reinforced",
                "fact_id": fact.id,
                "similarity": round(similarity, 3),
                "content": fact.content,
            }

        fact_id = self._store.add_fact(
            content=content,
            category=category,
            confidence=confidence,
            source=source,
            embedding=embedding,
        )

        return {
            "status": "stored",
            "fact_id": fact_id,
            "content": content,
        }

    def _find_similar(
        self,
        content: str,
        embedding: list[float] | None,
    ) -> tuple[Fact, float] | None:
        facts = self._store.all_facts()

        if not facts:
            return None

        if embedding is not None:
            best_fact: Fact | None = None
            best_score = 0.0

            for fact in facts:
                if fact.embedding is None:
                    continue

                score = cosine_similarity(embedding, fact.embedding)

                if score > best_score:
                    best_score = score
                    best_fact = fact

            if best_fact is not None and best_score >= DEDUPE_THRESHOLD:
                return best_fact, best_score

            return None

        # No embedding available (offline / API hiccup): fall back to
        # a crude exact-text check so we at least avoid literal dupes.
        normalized = content.strip().lower()

        for fact in facts:
            if fact.content.strip().lower() == normalized:
                return fact, 1.0

        return None

    # ----------------------------------------------------------------
    # Recall
    # ----------------------------------------------------------------

    def core_fact_ids(self, max_facts: int = 6) -> set[int]:
        """Ids of the always-on facts injected at connect time."""

        facts = self._store.all_facts()

        ranked = sorted(
            facts,
            key=lambda f: (f.times_reinforced, f.confidence, f.updated_at),
            reverse=True,
        )[:max_facts]

        return {f.id for f in ranked}

    def recall_context(self, max_facts: int = 6) -> str:
        """
        Build the block of remembered facts to inject into the
        system prompt. Falls back to the most recently reinforced
        facts if embeddings aren't available.
        """

        facts = self._store.all_facts()

        if not facts:
            return ""

        ranked = sorted(
            facts,
            key=lambda f: (f.times_reinforced, f.confidence, f.updated_at),
            reverse=True,
        )[:max_facts]

        lines = "\n".join(f"- {fact.content}" for fact in ranked)

        return (
            "Things Alfred already knows about this user and machine "
            "from previous sessions (treat as background knowledge, "
            "verify with a tool before acting on anything critical):\n"
            f"{lines}"
        )

    def _embed(self, text: str) -> list[float] | None:
        if self._embedder is None:
            return None

        try:
            return self._embedder.embed(text)
        except Exception as exc:  # noqa: BLE001
            print(f"[Memory] embedding failed: {exc}")
            return None

    def recall(self, query: str, top_k: int = 5) -> list[Fact]:
        facts = self._store.all_facts()

        if not facts:
            return []

        query_embedding = self._embed(query)

        if query_embedding is None:
            return facts[:top_k]

        scored: list[tuple[float, Fact]] = []

        for fact in facts:
            if fact.embedding is None:
                continue

            score = cosine_similarity(query_embedding, fact.embedding)

            if score >= RECALL_THRESHOLD:
                scored.append((score, fact))

        scored.sort(key=lambda pair: pair[0], reverse=True)

        return [fact for _, fact in scored[:top_k]]

    # ----------------------------------------------------------------
    # Passive learning (automatic, end of session)
    # ----------------------------------------------------------------

    def distill_session(self, transcript: list[dict[str, str]]) -> int:
        """
        Summarize a full session transcript into durable facts.
        Returns the number of new/reinforced facts.
        """

        if not transcript or self._chat is None:
            return 0

        formatted = "\n".join(
            f"{turn['role']}: {turn['text']}" for turn in transcript
        )

        prompt = DISTILLATION_PROMPT.format(transcript=formatted)

        try:
            raw_text = self._chat.generate(prompt, temperature=0.2)
        except Exception as exc:  # noqa: BLE001
            print(f"[Memory] session distillation failed: {exc}")
            return 0

        if not raw_text:
            return 0

        extracted = self._parse_facts(raw_text)

        count = 0

        for item in extracted:
            content = str(item.get("content", "")).strip()

            if not content:
                continue

            self.remember(
                content=content,
                category=str(item.get("category", "general")),
                confidence=float(item.get("confidence", 0.7)),
                source="distillation",
            )

            count += 1

        return count

    @staticmethod
    def _parse_facts(raw_text: str) -> list[dict[str, Any]]:
        cleaned = raw_text.strip()

        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            return []

        if not isinstance(parsed, list):
            return []

        return [item for item in parsed if isinstance(item, dict)]
