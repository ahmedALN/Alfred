"""Alfred, texting.

In the room there is no ambiguity about what a sentence is for: you say
"hello" and Alfred says hello back, and you say "open Steam" and it
opens Steam, because the live voice session hears both and only reaches
for a tool when there is something to reach for.

The phone had none of that. Every message went straight to the task
agent as a goal, so "Hello alfred" was a job, and the agent - being an
agent - did it: it opened Notepad and typed Hello. Then "Hello" and
"How are you" queued up behind it to be done in turn.

So a message gets read before it gets acted on. One model call decides
between the two and writes the answer at the same time, because they
are the same judgement: knowing that "can you play some music" is work
and "how are you" is not IS knowing what to say back.

When it cannot tell, it talks. A greeting mistaken for a job types into
Notepad; a job mistaken for a greeting gets a sentence asking what you
meant, and you say it again.
"""

from __future__ import annotations

from collections import deque
from typing import Callable

_SYSTEM = """You are Alfred, a butler-like assistant living on this \
person's Windows PC. They are texting you from their phone.

Every message is one of two things: something to SAY back, or something \
to DO on the computer.

Answer with ONE line, starting with SAY: or DO:

SAY: <your reply>
    For conversation, greetings, thanks, questions you can answer in \
words, and anything you are unsure about. Be brief - it is a text \
message, not an essay. One or two sentences.

DO: <the instruction, rewritten plainly>
    ONLY when they want something to happen on the computer: opening \
things, playing things, searching, files, settings, and so on. Rewrite \
it as a clear instruction, keeping every detail they gave.

Examples:
    "hey alfred"            -> SAY: Evening. What do you need?
    "how are you"           -> SAY: Running fine, nothing on. You?
    "you there?"            -> SAY: Here.
    "open steam"            -> DO: Open Steam.
    "put some music on"     -> DO: Open Spotify and start playing music.
    "whats the weather"     -> DO: Look up today's weather and report it.
    "did that work?"        -> SAY: <answer from what you know>
"""

_MAX_REPLY = 900


class Conversation:
    """One model call that both decides and answers."""

    def __init__(
        self,
        chat,
        submit: Callable[[str], object],
        remember: int = 6,
    ) -> None:
        self._chat = chat
        self._submit = submit
        self._history: deque[str] = deque(maxlen=remember)

    def handle(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""

        try:
            raw = self._chat.generate(
                self._prompt(text), system=_SYSTEM, temperature=0.3,
                max_tokens=200,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[Message] could not think of a reply: {exc}")
            # Silence is the one unacceptable answer.
            return "I'm having trouble thinking straight - say that again?"

        kind, body = _read(raw)

        if kind == "do":
            answer = self._start(body or text)
        else:
            answer = body or "Sorry - say that again?"

        self._history.append(f"Them: {text}")
        self._history.append(f"You: {answer}")
        return answer[:_MAX_REPLY]

    # ------------------------------------------------------------------

    def _prompt(self, text: str) -> str:
        if not self._history:
            return f"Message: {text}"
        recent = "\n".join(self._history)
        return f"Recently:\n{recent}\n\nMessage: {text}"

    def _start(self, job: str) -> str:
        try:
            self._submit(job)
        except Exception as exc:  # noqa: BLE001
            return f"I couldn't start that: {exc}"
        return "On it."


def _read(raw: str) -> tuple[str, str]:
    """Pull the decision out of the model's line.

    Anything unrecognised is treated as talk. That is the safe way
    round: the cost of mistaking a job for conversation is one more
    message; the cost of the reverse is Alfred loose on the desktop.
    """
    line = (raw or "").strip()
    if not line:
        return "say", ""

    # Small models like to wrap things. Find the marker anywhere.
    for marker, kind in (("DO:", "do"), ("SAY:", "say")):
        at = line.upper().find(marker)
        if at != -1:
            body = line[at + len(marker):].strip()
            return kind, body.splitlines()[0].strip() if body else ""

    return "say", line.splitlines()[0].strip()
