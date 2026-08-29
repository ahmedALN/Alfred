from __future__ import annotations

from typing import Any

from src.memory.learner import MemoryLearner
from src.tools.base import AlfredTool


class RememberTool(AlfredTool):
    """Lets Alfred explicitly commit something to long-term memory."""

    name = "remember"

    description = (
        "Permanently remember a fact about the user, their machine, "
        "their preferences, or a correction they gave you about your "
        "own behavior, so you recall it in future sessions. Use this "
        "any time the user states a preference, corrects you, or you "
        "learn something stable about their setup (e.g. app names, "
        "device names, habits, recurring instructions). Do not use "
        "this for one-off task details that only matter right now."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": (
                        "The fact to remember, written as one "
                        "self-contained sentence."
                    ),
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "preference",
                        "system",
                        "habit",
                        "correction",
                        "general",
                    ],
                    "description": "What kind of fact this is.",
                },
            },
            "required": ["content"],
        }

    def __init__(self, learner: MemoryLearner) -> None:
        self._learner = learner

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        content = arguments.get("content")
        category = arguments.get("category", "general")

        if not isinstance(content, str):
            raise ValueError("'content' must be a string.")

        return self._learner.remember(content=content, category=category)


class ForgetTool(AlfredTool):
    """Lets the user tell Alfred to delete something it remembers."""

    name = "forget"

    description = (
        "Delete a fact from long-term memory when the user says to forget "
        "something. Call with a short description of what to forget; it "
        "returns the matching facts. To actually delete, call again with "
        "the same query plus '_confirmed': true, and it removes the best "
        "match."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "_confirmed": {"type": "boolean"},
            },
            "required": ["query"],
        }

    def __init__(self, learner: MemoryLearner) -> None:
        self._learner = learner

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return {"status": "error", "error": "'query' must be a string."}

        matches = self._learner.recall(query, top_k=5)

        if not matches:
            return {"status": "not_found",
                    "note": "Nothing in memory matches that."}

        if not arguments.get("_confirmed"):
            return {
                "status": "needs_confirmation",
                "matches": [{"content": f.content} for f in matches],
                "instruction": (
                    "Read the top match to the user and ask if that's the "
                    "one to forget. If yes, call forget again with the same "
                    "query and '_confirmed': true."
                ),
            }

        target = matches[0]
        self._learner._store.delete_fact(target.id)
        return {"status": "success", "forgot": target.content}


class RecallTool(AlfredTool):
    """Lets Alfred deliberately search its own long-term memory."""

    name = "recall"

    description = (
        "Search your long-term memory for facts relevant to a topic. "
        "Use this when you need to check what you already know before "
        "asking the user something you might have been told before, "
        "or before assuming details about their system."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search your memory for.",
                },
            },
            "required": ["query"],
        }

    def __init__(self, learner: MemoryLearner) -> None:
        self._learner = learner

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments.get("query")

        if not isinstance(query, str):
            raise ValueError("'query' must be a string.")

        facts = self._learner.recall(query)

        return {
            "status": "success",
            "results": [
                {
                    "content": fact.content,
                    "category": fact.category,
                    "confidence": fact.confidence,
                }
                for fact in facts
            ],
        }
