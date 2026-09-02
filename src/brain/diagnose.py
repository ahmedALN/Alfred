"""Reading the error before deciding what to do about it.

When a step failed, Alfred replanned. It handed the planner the words
"not verified" and whatever evidence was to hand, and asked for a
different plan. What it never did was look at what actually went wrong.

The cost of that shows up twice.

It retries the same wall. A control that cannot be found because the
window was never read is a different problem from one that cannot be
found because the app is not open, and they want different next moves;
told only "failed", the planner picks between them by luck.

And it invents causes. The post-mortem was shown a trace reading
`skill(...) -> FAILED` with no error text at all, so it wrote down
"The skill tool does not support list or learn actions" - which is
false, and which is now one of sixty-nine such lessons sitting in
memory, each one a thing Alfred will not try again.

So: read the error, name the cause, and only then decide. Most failures
are one of a dozen shapes and are recognised here without asking a
model at all - which is cheaper, and which cannot hallucinate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class Diagnosis:
    """What went wrong, and what that means for the next move."""

    cause: str          # in plain words, for a person and for a prompt
    suggestion: str     # what to do instead
    certain: bool       # recognised outright, or guessed at
    fatal: bool = False  # no amount of retrying will help

    def __str__(self) -> str:
        return f"{self.cause} {self.suggestion}".strip()


# Error shapes seen often enough to be worth naming. Each is (pattern,
# cause, what to do instead, fatal).
#
# The order matters: the first match wins, so the specific ones come
# before the general.
_KNOWN: list[tuple[str, str, str, bool]] = [
    (r"no control matches.*name=",
     "the control it wanted was not in the window's accessibility tree",
     "Read the window with ui_control tree first and use a name that is "
     "really there, or check the app is open and finished loading.",
     False),

    (r"window not found|wait_ready.*timed out",
     "the window it expected is not open, or is titled differently",
     "Open the app first, or list windows with ui_control windows and use "
     "the title that is actually there.",
     False),

    (r"must be a non-empty string|needs '(\w+)'|is missing \[",
     "the call was made without an argument the tool requires",
     "Supply the missing argument. Re-read the tool's arguments in the "
     "catalogue rather than guessing at the name.",
     False),

    (r"action must be one of|must be one of \[",
     "the action name does not exist for that tool",
     "Use one of the actions the catalogue lists in [brackets]; they are "
     "the only valid values.",
     False),

    (r"timed out after",
     "the command hung rather than failing",
     "Something is waiting for input that will never come. Use a "
     "non-interactive form of the command, or a different tool.",
     False),

    (r"access is denied|unauthorized|requires elevation|administrator",
     "Windows refused it for want of permission",
     "This needs elevation, which Alfred does not have. Say so and let "
     "the user run it themselves - do not retry.",
     True),

    # Past and present: the agent writes "needed your OK", the tool
    # writes "needs_confirmation". Matching only one of them is how a
    # pattern quietly covers half the cases it was written for.
    (r"need(s|ed) (the user's|your) OK|needs_confirmation|user declined",
     "the step needed permission and there was no way to ask",
     "Do not retry. Tell the user what needs confirming and let them "
     "say yes.",
     True),

    (r"not found|nothing came back|no results|empty",
     "the thing was looked for and is genuinely not there",
     "Check the spelling or widen the search before concluding it does "
     "not exist.",
     False),

    (r"is not a tool|unknown alfred tool",
     "it called a tool that does not exist",
     "Use only tools from the catalogue.",
     False),

    (r"connection|network|unreachable|timed out.*http|ssl",
     "the network call did not get through",
     "Try once more; if it fails again the site or service is down and "
     "that is worth saying rather than working around.",
     False),
]


def _text_of(result: Any) -> str:
    """The error, wherever the tool decided to put it."""
    if isinstance(result, dict):
        for key in ("error", "stderr", "message", "reason", "detail"):
            found = str(result.get(key) or "").strip()
            if found:
                return found
        status = str(result.get("status") or "").strip()
        # A tool that came back "success" with nothing in it is its own
        # kind of failure, and the one that made Alfred say it had sent
        # a screenshot it had not.
        if status and status != "success":
            return status
    if isinstance(result, str):
        return result.strip()
    return ""


def recognise(error: str) -> Diagnosis | None:
    """A known shape, named without asking anybody."""
    text = (error or "").lower()
    if not text:
        return None
    for pattern, cause, suggestion, fatal in _KNOWN:
        if re.search(pattern, text, re.I):
            return Diagnosis(cause, suggestion, certain=True, fatal=fatal)
    return None


_SYSTEM = """You are reading one failed step of a task on a Windows PC and \
saying what actually went wrong.

Answer in exactly two lines:
CAUSE: <what went wrong, one sentence, concrete>
INSTEAD: <what to do differently, one sentence>

Rules:
- Say only what the error text supports. If it does not say why, say that.
- Never blame a tool for not having a feature unless the error says so.
- Do not restate the error; explain it."""


def diagnose(
    tool: str,
    args: dict[str, Any],
    result: Any,
    goal: str = "",
    chat: Any = None,
) -> Diagnosis:
    """Why that step failed, and what to try instead.

    Known shapes are recognised outright. Anything else gets one cheap
    model call - and if that is unavailable, an honest "not known"
    rather than a guess dressed as a finding.
    """
    error = _text_of(result)

    known = recognise(error)
    if known is not None:
        return known

    if not error:
        return Diagnosis(
            "the tool failed without saying why",
            "Try a different tool or a smaller step; there is nothing here "
            "to learn from.",
            certain=False,
        )

    if chat is None:
        return Diagnosis(error[:200], "", certain=False)

    try:
        answer = chat.generate(
            f"{_SYSTEM}\n\nGOAL: {goal}\nSTEP: {tool}({args})\n"
            f"ERROR: {error[:600]}\n\nYour two lines:",
            temperature=0.1, max_tokens=200,
        )
    except Exception:  # noqa: BLE001
        return Diagnosis(error[:200], "", certain=False)

    cause, suggestion = "", ""
    for line in (answer or "").splitlines():
        line = line.strip()
        if line.upper().startswith("CAUSE:"):
            cause = line.split(":", 1)[1].strip()
        elif line.upper().startswith("INSTEAD:"):
            suggestion = line.split(":", 1)[1].strip()

    return Diagnosis(
        cause or error[:200], suggestion, certain=False,
    )


# A bare status is not an explanation. "error" and "failed" are what a
# tool says when it has nothing to tell you.
_SAYS_NOTHING = {"", "error", "failed", "failure", "auto", "none", "unknown"}

# Claims that a tool lacks a capability. These are the dangerous ones:
# they are permanent, they are usually wrong, and they stop Alfred
# reaching for that tool ever again.
_LACKS = ("does not", "doesn't", "cannot", "can't", "is not able to",
          "lacks", "has no", "no longer supports")
_ABILITY = ("support", "allow", "have", "provide", "accept", "handle")


def _claims_a_tool_cannot(lesson: str) -> bool:
    """"The skill tool does not support list actions" and its kin.

    Written as words rather than a regex on purpose: this file has now
    twice had its word-boundary escapes turned into literal backspace
    characters on the way in, which produces a pattern that silently
    matches nothing at all.
    """
    text = lesson.lower()
    if not any(word in text for word in ("tool", "command", "api")):
        return False
    return (any(word in text for word in _LACKS)
            and any(word in text for word in _ABILITY))


def supported(lesson: str, errors: list[str]) -> bool:
    """Is this lesson something the evidence actually says?

    The post-mortem used to be shown "FAILED" with no error text, so it
    filled the gap itself. "The skill tool does not support list or
    learn actions" is false, was written from a trace that said nothing
    of the kind, and is one of sixty-nine such entries.

    Two rules, both narrow, because rejecting real lessons would trade
    one problem for another:

    Nothing informative failed -> nothing was learned. A step that came
    back "error" with no message cannot teach you anything.

    A claim that a tool CANNOT do something has to be quoted, not
    inferred. Everything else is allowed: a model generalising from a
    real error to a real lesson is the whole point of asking it.
    """
    lesson = (lesson or "").strip()
    if len(lesson) < 8:
        return False

    real = [
        e for e in errors
        if e and e.strip().lower() not in _SAYS_NOTHING
    ]
    if not real:
        return False

    if _claims_a_tool_cannot(lesson):
        haystack = " ".join(real).lower()
        # The error has to say so itself.
        return any(
            phrase in haystack
            for phrase in ("not support", "unsupported", "no such",
                           "unknown action", "must be one of",
                           "not a tool", "no attribute")
        )

    return True


_EVERYWHERE = {
    "this", "that", "with", "from", "when", "then", "than", "have", "will",
    "should", "would", "could", "must", "need", "needs", "using", "used",
    "into", "onto", "does", "doing", "make", "made", "take", "taken",
    "step", "steps", "task", "tasks", "tool", "tools", "alfred", "user",
    "instead", "because", "there", "their", "they", "were", "before",
    "after", "which", "while", "about", "always", "never", "cannot",
    "failed", "failure", "error", "errors", "attempt", "attempted",
}
