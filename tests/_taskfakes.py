"""Shared fakes for the plan/execute/verify TaskAgent tests."""

from __future__ import annotations

import json
from typing import Any, Callable


def _line_after(prompt: str, marker: str) -> str:
    idx = prompt.find(marker)
    if idx == -1:
        return ""
    return prompt[idx + len(marker):].splitlines()[0].strip()


class DispatchChat:
    """A fake ChatProvider that answers by which Alfred prompt it sees.

    - planner prompts  -> pops from ``plan`` (dicts or JSON strings); a bare
      list is taken as the ``plan`` array and wrapped.
    - executor prompts -> pops from ``steps[key]`` where ``key`` is a substring
      of the current step text; falls back to action=done.
    - verify prompts   -> ``verify`` may be a bool, ``None`` (=> True), or a
      ``Callable[[str], bool]`` taking the step text.
    """

    name = "dispatch"
    model = "dispatch"

    def __init__(
        self,
        *,
        plan: list[Any] | None = None,
        steps: dict[str, list[Any]] | None = None,
        verify: bool | None | Callable[[str], bool] = True,
        plan_raises: BaseException | None = None,
    ) -> None:
        self._plan = list(plan or [])
        self._steps = {k: list(v) for k, v in (steps or {}).items()}
        self._verify = verify
        self._plan_raises = plan_raises
        self.prompts: list[str] = []
        self.plan_calls = 0
        self.verify_calls = 0

    def generate(self, prompt: str, **_kw: Any) -> str:
        self.prompts.append(prompt)

        if "Alfred's task planner" in prompt:
            self.plan_calls += 1
            if self._plan_raises is not None:
                raise self._plan_raises
            item = self._plan.pop(0) if self._plan else {"plan": []}
            if isinstance(item, list):
                item = {"plan": item}
            return item if isinstance(item, str) else json.dumps(item)

        if "check whether a task step" in prompt:
            self.verify_calls += 1
            step = _line_after(prompt, "STEP:")
            ok = self._verify(step) if callable(self._verify) else self._verify
            if ok is None:
                ok = True
            return f"{'VERIFIED' if ok else 'UNVERIFIED'}: test-check"

        # executor
        step = _line_after(prompt, "CURRENT STEP:")
        for key, replies in self._steps.items():
            if key in step and replies:
                r = replies.pop(0)
                return r if isinstance(r, str) else json.dumps(r)
        return json.dumps({"action": "done", "evidence": "nothing to do"})


class FakeRegistry:
    def __init__(self, results: dict[str, Any] | None = None) -> None:
        self.results = results or {}
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def gemini_declarations(self) -> list[dict[str, str]]:
        return [
            {"name": "ui_control", "description": "read/click app controls"},
            {"name": "desktop_control", "description": "see and click"},
            {"name": "open_app", "description": "launch an app"},
            {"name": "system_info", "description": "read system"},
            {"name": "powershell", "description": "run powershell"},
        ]

    def names(self) -> list[str]:
        return [
            "ui_control", "desktop_control", "open_app",
            "system_info", "powershell",
        ]

    def execute(self, name: str, args: dict[str, Any]) -> Any:
        self.executed.append((name, args))
        return self.results.get(name, {"status": "success"})


KNOWN = {
    "ui_control", "desktop_control", "open_app", "system_info", "powershell",
}
