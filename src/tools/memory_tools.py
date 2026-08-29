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
