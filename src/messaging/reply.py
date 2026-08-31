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

Answer with ONE line, starting with SAY:, DO:, SHOW: or STEER:

SAY: <your reply>
    For conversation, greetings, thanks, questions you can answer in \
words, and anything you are unsure about. Be brief - it is a text \
message, not an essay. One or two sentences.

DO: <the instruction, rewritten plainly> || <what you say back>
    ONLY when they want something to happen on the computer. \
Rewrite it as a clear instruction, keeping every detail they gave, then \
|| and what you are telling them, which should show you understood \
the particular thing they asked for. Never just "On it." - say what \
it is you are going off to do.

What you can actually do, so you never claim otherwise: you see \
the screen, you read and click the controls of any open app, you \
open and close programs, you type, you browse and search the web, \
you handle files and settings, and you run long jobs in the \
background. Anything asking about the state of the machine right \
now - what is on screen, what is running, what is playing - is \
something to DO, because finding out means going and looking.

STEER: <what to change>
    ONLY when RUNNING BELOW says a job is in progress AND the message
    is about that job - a correction, a change of mind, a detail it
    got wrong. "no not that one", "make it the 1.21.11 instance",
    "search for Hollow Knight instead", "do it on the other screen".
    The running job reads it before its next move.
    If nothing is running, this is not an option - it is a new DO.

SHOW: picture
SHOW: clip <seconds>
    When they want to SEE the screen rather than be told about it -
    "send me a screenshot", "what does it look like", "show me",
    "record the screen for 10 seconds". This sends them the actual
    picture or video, which is faster and better than describing it.
    Default to picture. Use clip only when they ask for a recording,
    a video, or something happening over time.

Examples:
    "hey alfred"            -> SAY: Evening. What do you need?
    "how are you"           -> SAY: Running fine, nothing on. You?
    "you there?"            -> SAY: Here.
    "open steam"            -> DO: Open Steam. || Opening Steam now.
    "put some music on"     -> DO: Open Spotify and play something. || Putting something on now.
    "whats the weather"     -> DO: Look up today's weather. || Checking the forecast.
    "whats on my screen"    -> DO: Look at the screen and describe it. || Having a look.
    "is steam still open"   -> DO: Check whether Steam is running. || Checking.
    "did that work?"        -> SAY: <answer from what you know>
    "send me a screenshot"  -> SHOW: picture
    "show me the screen"    -> SHOW: picture
    "record my screen 10s"  -> SHOW: clip 10
    "no not that one"      -> STEER: not that one - pick a different result
    "make it 1.21.11"      -> STEER: use the 1.21.11 instance instead
"""

_MAX_REPLY = 900


class Conversation:
    """One model call that both decides and answers."""

    def __init__(
        self,
        chat,
        submit: Callable[[str], object],
        remember: int = 6,
        screen=None,
        steer: "Callable[[str], bool] | None" = None,
        running: "Callable[[], str] | None" = None,
    ) -> None:
        self._chat = chat
        self._submit = submit
        self._screen = screen
        # Saying something to work in progress, rather than starting a
        # second job that fights the first.
        self._steer = steer
        self._running = running
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

        if kind == "steer":
            answer = self._change(body or text)
        elif kind == "show":
            answer = self._show(body)
        elif kind == "do":
            job, said = _split(body or text)
            answer = self._start(job, said)
        else:
            answer = body or "Sorry - say that again?"

        self._history.append(f"Them: {text}")
        self._history.append(f"You: {answer}")
        return answer[:_MAX_REPLY]

    # ------------------------------------------------------------------

    def _prompt(self, text: str) -> str:
        parts = []
        if self._history:
            parts.append("Recently:" + chr(10) + chr(10).join(self._history))

        # Whether anything is running decides whether STEER is even
        # on the table, so it goes in front of the model every time.
        busy = ""
        if self._running is not None:
            try:
                busy = (self._running() or "").strip()
            except Exception:  # noqa: BLE001
                busy = ""
        parts.append(
            f"RUNNING NOW: {busy}" if busy
            else "RUNNING NOW: nothing - STEER is not an option"
        )

        parts.append(f"Message: {text}")
        return (chr(10) + chr(10)).join(parts)

    def _start(self, job: str, said: str = "") -> str:
        try:
            self._submit(job)
        except Exception as exc:  # noqa: BLE001
            return f"I couldn't start that: {exc}"
        # "On it." says only that a message was received. What was
        # understood is the part worth hearing, and it is the part that
        # lets you correct Alfred before it has done the wrong thing.
        return said or f"Right - {job[0].lower()}{job[1:].rstrip('.')}."


    def _change(self, what: str) -> str:
        """Pass a correction to the job that is running."""
        if self._steer is None or not self._steer(what):
            # Nothing running to correct, so it was a new request all
            # along. Better to do the thing than to explain the
            # distinction to somebody who does not care about it.
            return self._start(what)
        return f"Right - {what}"

    def _show(self, what: str) -> str:
        """Send the screen itself rather than a description of it."""
        if self._screen is None:
            return "I can't send pictures on this channel."

        want = (what or "").strip().lower()
        if want.startswith("clip") or "record" in want:
            return self._screen.clip(_seconds(want))
        return self._screen.picture()


def _seconds(want: str) -> int:
    from src.messaging.capture import DEFAULT_SECONDS

    digits = "".join(c if c.isdigit() else " " for c in want).split()
    return int(digits[0]) if digits else DEFAULT_SECONDS


def _split(body: str) -> tuple[str, str]:
    """"Open Steam. || Opening Steam now." -> the job, and the words.

    Both come out of the one call because they are one thought. Asking
    for them separately would cost a second round trip to be told again
    what had just been decided.
    """
    job, sep, said = body.partition("||")
    return job.strip(), (said.strip() if sep else "")


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
    for marker, kind in (
        ("STEER:", "steer"), ("SHOW:", "show"), ("DO:", "do"), ("SAY:", "say"),
    ):
        at = line.upper().find(marker)
        if at == -1:
            continue
        body = line[at + len(marker):].strip()
        # Models sometimes echo the marker back before answering.
        while body.upper().startswith(marker):
            body = body[len(marker):].strip()
        return kind, body.splitlines()[0].strip() if body else ""

    return "say", line.splitlines()[0].strip()
