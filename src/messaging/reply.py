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
from collections.abc import Callable

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

"Learn HOW TO x", "remember HOW TO x", "always do x this way" is a \
request for Alfred to ACQUIRE A SKILL, not to go and read about x. \
Pass it through as the learning job it is - "Learn a routine for x" - \
and never rewrite it into a web search. The difference matters: one \
leaves Alfred able to do the thing afterwards, the other leaves it \
having read an article.

"Remember THAT x", "note that x", "don't forget x", "I like/hate x" is \
something entirely different: a FACT about the person, to be kept. That \
is a DO, and the instruction is "Remember that x" - never "learn a \
routine". A routine is a sequence of actions; "I hate coriander" is not \
a sequence of actions.
    "remember that I hate coriander" -> DO: Remember that the user hates coriander. || Noted.
    "remember how to open my games"  -> DO: Learn a routine for opening the games. || Working that out.

What you can actually do, so you never claim otherwise: you see \
the screen, you read and click the controls of any open app, you \
open and close programs, you type, you browse and search the web, \
you handle files and settings, and you run long jobs in the \
background. Anything asking about the state of the machine right \
now - what is on screen, what is running, what is playing - is \
something to DO, because finding out means going and looking.

That includes questions about YOURSELF. What you have learned, which \
routines you know, what you remember, what you cannot do, what is on \
your schedule - none of that is in your head, it is in stores you have \
to go and read. Asked any of it, DO, never SAY. You have no idea how \
many routines you know until you look, and guessing is how you end up \
telling someone you have learned nothing when you have learned thirty-nine.
    "what have you learned"    -> DO: List the routines you have learned. || Let me look.
    "what do you remember"     -> DO: List what you remember about the user. || Checking.
    "what can't you do"        -> DO: List the limitations you have recorded. || Having a look.

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
    "learn how to search steam" -> DO: Learn a routine for searching Steam. || Working that out and keeping it.
    "send me a screenshot"  -> SHOW: picture
    "show me the screen"    -> SHOW: picture
    "record my screen 10s"  -> SHOW: clip 10
    "no not that one"      -> STEER: not that one - pick a different result
    "make it 1.21.11"      -> STEER: use the 1.21.11 instance instead
"""

_MAX_REPLY = 900



# Stopping, and forbidding. A message carrying either must never become
# a job, whatever a model makes of it.

_BARE_STOP = {
    "stop", "cancel", "abort", "halt", "quit it", "never mind",
    "nevermind", "forget it", "leave it", "drop it", "enough",
    "stop it", "stop that", "stop please", "cancel that", "cancel it",
}

# What "stop" has to be pointed at for it to mean the running job.
# "stop the music" is an instruction about Spotify, not about Alfred.
_THE_JOB = (
    "that", "it", "this", "the task", "the job", "doing", "what you",
    "everything", "all of it", "the search", "searching",
)

# Real prohibitions only. "instead of" and "not that one" are
# corrections, and a running job reads those as steers through the
# ordinary path - they are not stop words.
_FORBIDS = ("don't ", "dont ", "do not ", "no longer", "no more", "never ")


def is_stop(text: str) -> bool:
    """Does this ask for the running job to end?"""
    said = (text or "").strip().lower().strip("!.? ")
    if not said:
        return False
    if said in _BARE_STOP:
        return True

    for word in ("stop", "cancel", "abort", "halt"):
        if said.startswith(word + " "):
            rest = said[len(word) + 1:].strip()
            # "stop that" yes; "stop the music" no - that one is a
            # thing to go and do, not a job to abandon.
            return any(rest.startswith(target) for target in _THE_JOB)
    return False


def forbids(text: str) -> bool:
    """Does this tell Alfred NOT to do something?

    Told "stop that, do not search for how to fish", the model dropped
    the negation and answered DO: Open a browser and search for how to
    fish. Alfred then went and did it, twenty-seven seconds after being
    told not to. Small models lose "not" often enough that this cannot
    be left to one.
    """
    said = " " + (text or "").strip().lower() + " "
    return any(word in said for word in _FORBIDS)


class Conversation:
    """One model call that both decides and answers."""

    def __init__(
        self,
        chat,
        submit: Callable[[str], object],
        remember: int = 6,
        screen=None,
        steer: Callable[[str], bool] | None = None,
        running: Callable[[], str] | None = None,
        eyes=None,
        record=None,
        cancel=None,
    ) -> None:
        self._chat = chat
        self._submit = submit
        self._screen = screen
        # Saying something to work in progress, rather than starting a
        # second job that fights the first.
        self._steer = steer
        self._running = running
        # Stopping is not steering: "stop" wants the job to end, not to
        # continue differently.
        self._cancel = cancel
        # For looking at what you send it. Alfred could be talked to and
        # not shown anything, which rules out most of the reasons a
        # person picks up their phone: this error, this letter, this
        # thing in front of me.
        self._eyes = eyes
        # Written to the same place the voice conversation goes, so what
        # you said on the phone is known in the room and the other way
        # round. One thread, two doors into it.
        self._record = record
        self._history: deque[str] = deque(maxlen=remember)

    def handle(self, text: str, media: bytes | None = None,
               kind: str = "") -> str:
        text = (text or "").strip()

        # A picture is the message. Answer about it rather than routing
        # it - "what is this?" is not a job for the task agent.
        if media:
            return self._look(text, media, kind)

        if not text:
            return ""

        # A prohibition is never a job. This is decided before the model
        # sees it, because the model inverted one: "stop that, do not
        # search for how to fish" came back as DO: search for how to
        # fish, and Alfred went and did it.
        if is_stop(text) or forbids(text):
            answer = self._halt(text)
            self._history.append(f"Them: {text}")
            self._history.append(f"You: {answer}")
            self._keep(text, answer)
            return answer[:_MAX_REPLY]

        # Decided here rather than by the model, because the model got
        # it wrong repeatedly and then imitated its own wrong answers.
        want = wants_picture(text)
        if want and self._screen is not None:
            # For a clip the original words go through too, so "10s"
            # survives to be read as ten seconds.
            answer = self._show(f"clip {text}" if want == "clip" else "picture")
            self._history.append(f"Them: {text}")
            if answer:
                self._history.append(f"You: {answer}")
            self._keep(text, answer)
            return answer[:_MAX_REPLY]

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

        if kind == "unclear" or not body:
            # It thought out loud instead of answering. Ask again
            # rather than forward the thinking or act on it.
            print(f"[Message] unreadable answer, not forwarding: {raw[:120]!r}")
            answer = "Sorry - say that again?"
        elif kind == "steer":
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
        self._keep(text, answer)
        return answer[:_MAX_REPLY]

    # ------------------------------------------------------------------

    def _keep(self, said: str, answered: str) -> None:
        """Into the shared thread, so the room knows about the phone."""
        if self._record is None:
            return
        try:
            self._record("user", said)
            if answered:
                self._record("alfred", answered)
        except Exception:  # noqa: BLE001
            pass

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



    def _halt(self, text: str) -> str:
        """Stop, or steer away from - but never start anything.

        The one thing that must not happen here is a new job. A message
        saying not to do something has, by definition, nothing in it
        that anybody asked for.
        """
        running = self._running() if self._running else ""

        if is_stop(text):
            if not running:
                return "Nothing running to stop."
            if self._cancel is not None:
                self._cancel()
                return f"Stopped - {running[0].lower()}{running[1:].rstrip('.')}."
            return "I can't stop that from here."

        # A prohibition while something runs is a correction to it.
        if running and self._steer is not None and self._steer(text):
            return f"Right - {text}"

        return (
            "Noted - I won't. Nothing is running, so there was nothing "
            "to stop."
        )

    def _look(self, text: str, media: bytes, kind: str) -> str:
        """Say what is in the thing they sent."""
        if self._eyes is None:
            return (
                "I can see it arrived but I have no way to look at it "
                "right now."
            )
        if kind == "video":
            return (
                "That's a video - I can only look at still pictures so "
                "far. A screenshot of the moment would work."
            )

        # Whatever they asked, the answer is going to a phone. Nobody
        # wants six headings and a bulleted list about a photo they
        # just took.
        asked = (
            (text or "What is this?")
            + " Answer in two or three short sentences, plain text - no "
            "markdown, no headings, no bullet points. If there is "
            "something in it they would obviously want pointed out, say "
            "that too."
        )
        try:
            seen = self._eyes.analyze(
                media, asked, mime_type="image/jpeg",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[Message] could not look at that: {exc}")
            return "I couldn't get a look at that one - send it again?"

        answer = (seen or "").strip()[:_MAX_REPLY]
        self._history.append(f"Them: [a picture] {text}"[:200])
        self._history.append(f"You: {answer}"[:200])
        self._keep(f"[a picture] {text}", answer)
        return answer or "I can see it, but I can't make anything out."

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



# Words that can only mean "send me the screen itself".
_PICTURE_WORDS = (
    "screenshot", "screen shot", "screengrab", "screen grab",
    "print screen", "printscreen",
)
_SEND_WORDS = ("send", "show", "give", "share", "let me see", "can i see")
_SCREEN_WORDS = ("screen", "desktop", "my pc", "my computer")
_RECORD_WORDS = ("record", "recording", "clip", "video of")


def wants_picture(text: str) -> str:
    """"" | "picture" | "clip" - decided without asking a model.

    Alfred spent half an hour telling somebody "Here is your
    screenshot" and sending nothing. The model was choosing SAY over
    SHOW, and once one of those answers was in the history it copied
    itself: every later reply promised a picture that never came.

    A request this unambiguous should never have depended on a model
    picking the right label. It is also two seconds faster.
    """
    said = (text or "").strip().lower()
    if not said:
        return ""

    recording = any(w in said for w in _RECORD_WORDS)
    named = any(w in said for w in _PICTURE_WORDS)

    if named:
        return "clip" if recording else "picture"

    on_screen = any(w in said for w in _SCREEN_WORDS)

    # "record my screen for 10 seconds" - asking to record it is asking
    # for it, whether or not you also said "send".
    if recording and on_screen:
        return "clip"

    # "send me the screen", "show me my desktop" - a send word and a
    # screen word together. "what is on my screen" has no send word and
    # stays a question for the task agent to answer in words.
    if any(w in said for w in _SEND_WORDS) and on_screen:
        return "picture"

    return ""


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


_MARKERS = (("STEER:", "steer"), ("SHOW:", "show"), ("DO:", "do"), ("SAY:", "say"))

# How much may sit in front of a marker and still count as wrapping.
# "Answer: SAY: hi" is a wrapper. A marker forty words into a paragraph
# is the model talking ABOUT markers, not using one.
_WRAPPER_SLACK = 24


def _trim(body: str) -> str:
    """Cut at the next marker.

    A body that runs on into another marker is reasoning about the
    format rather than an answer in it.
    """
    upper = body.upper()
    cut = len(body)
    for marker, _ in _MARKERS:
        at = upper.find(marker)
        if at != -1:
            cut = min(cut, at)
    # Models bold the marker as often as not, which leaves the closing
    # asterisks sitting at the front of the answer.
    return body[:cut].strip().strip("*\"'` ").strip()


def _read(raw: str) -> tuple[str, str]:
    """Pull the decision out of the model's answer.

    The contract is one line beginning with a marker. Small models do
    not always keep to it - they think out loud first, and sometimes
    mention the markers while doing so.

    That thinking is not a reply. It went out to WhatsApp verbatim:

        Right - " For screenshot request, we should use SHOW: picture.
        But we also need to bring Claude to foreground before taking

    because the marker was found anywhere in the text and everything
    after it became the body. Worse, when the marker was DO: that
    reasoning was submitted as the task goal, so Alfred set off to do
    a paragraph about itself.

    So: an anchored marker first, a wrapped one second, and if neither
    is there the answer is "unclear" - never the raw text. Silence is
    recoverable; forwarding the model's inner monologue is not.
    """
    text = (raw or "").strip()
    if not text:
        return "say", ""

    # 1. What was actually asked for: a marker starting a line.
    for line in text.splitlines():
        stripped = line.strip().lstrip("\"'*-# ").strip()
        for marker, kind in _MARKERS:
            if stripped.upper().startswith(marker):
                body = stripped[len(marker):].strip()
                while body.upper().startswith(marker):
                    body = body[len(marker):].strip()
                return kind, _trim(body)

    # 2. A marker with only a label in front of it.
    for line in text.splitlines():
        upper = line.upper()
        for marker, kind in _MARKERS:
            at = upper.find(marker)
            if at != -1 and at <= _WRAPPER_SLACK:
                return kind, _trim(line[at + len(marker):].strip())

    # 3. No marker anywhere usable. Unmarked text is normally just
    #    talk, and treating it as such is the safe way round: a
    #    greeting mistaken for a job types into Notepad, while a job
    #    mistaken for a greeting costs one more message.
    #
    #    The exception is text that discusses the format itself. Both
    #    of the answers that leaked to a phone named the markers while
    #    reasoning about which to use, and a real reply never does.
    if _is_reasoning(text):
        return "unclear", ""

    return "say", text.splitlines()[0].strip()


def _is_reasoning(text: str) -> bool:
    """Is this the model working out what to say, rather than saying it?"""
    upper = text.upper()
    # Anchored and wrapped markers were already taken, so one still
    # sitting in here is being talked about rather than used.
    if any(marker in upper for marker, _ in _MARKERS):
        return True
    return "THE INSTRUCTION" in upper

