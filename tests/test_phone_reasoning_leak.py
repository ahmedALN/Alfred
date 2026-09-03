"""Steering a running job produced "unreadable answer, not forwarding" -
and, worse, sometimes produced a reply that WAS forwarded, and was the
model's own reasoning about the problem.

Live, mid-task: told "nevermind, learn how to play songs of spotify"
while Spotify was still being worked on, the plan chain fell over to
nemotron - and nemotron, given only 200 tokens, spent every one of them
on "We need to interpret the user's message: ..." without ever reaching
a SAY:/DO:/STEER: line. Four out of four times.

That is two separate bugs, not one:

  - the token budget was too small for a reasoning model to ever
    finish reasoning and answer, so it failed EVERY time rather than
    sometimes
  - and when a similar opening slips through without tripping the
    marker check, the old `_is_reasoning` guard did not catch it -
    "We need to interpret..." contains no marker and no "THE
    INSTRUCTION", so it fell through to "unmarked text is just talk"
    and the model's own internal monologue would have been read out
    as if Alfred had said it
"""

from __future__ import annotations

import pytest

from src.messaging.reply import _TOKENS, _is_reasoning, _read

# ====================================================================
# The real transcript
# ====================================================================


def test_the_live_failure_is_recognised_as_reasoning():
    """Real text, truncated at 200 tokens, from the session that
    prompted this."""
    raw = (
        'We need to interpret the user\'s message: "nevermind, learn '
        'how to play songs of spotify". The current running job: "Open'
    )

    assert _read(raw) == ("unclear", "")


def test_the_token_budget_is_large_enough_for_a_reasoning_model():
    """200 was a one-liner's budget, never revisited for what a
    reasoning model actually needs. Measured against the exact call
    that failed: nemotron spent the whole 200-token and 300-token
    budgets on reasoning, every time, and reliably reached the marker
    at 700."""
    assert _TOKENS >= 700


# ====================================================================
# Reasoning openings the safety net has to catch
# ====================================================================


@pytest.mark.parametrize("opening", [
    'We need to interpret the user\'s message: "nevermind, learn how '
    'to play songs of spotify". The current running job: "Open',
    "I need to understand what they meant before answering the question.",
    "Let's figure out whether this is a job or a greeting first, then decide.",
    "Let me think about this for a moment before I answer.",
    "We need to work out the right response here given the context.",
    "We should decide whether this is a DO or a SAY before replying.",
    "I must determine what the user actually wants from this message.",
])
def test_a_reasoning_opening_is_never_forwarded_as_a_reply(opening):
    kind, body = _read(opening)

    assert kind == "unclear"
    assert body == ""


@pytest.mark.parametrize("opening", [
    'We need to interpret the user\'s message',
    "I need to understand this",
    "Let's figure out the plan",
    "Let me think",
    "We must determine the answer",
])
def test_is_reasoning_catches_it_directly(opening):
    assert _is_reasoning(opening)


# ====================================================================
# Ordinary replies must not be caught by the same net
# ====================================================================


@pytest.mark.parametrize("text,kind", [
    ("Hi there! Nothing running, what do you need?", "say"),
    ("I understand your request completely.", "say"),
    ("We need more milk, by the way.", "say"),
    ("I will let you know when it's done.", "say"),
    ("SAY: Running fine, thanks. You?", "say"),
    ("DO: Open Steam. || Opening Steam now.", "do"),
    ("STEER: use the 1.21.11 instance instead", "steer"),
])
def test_an_ordinary_reply_is_not_mistaken_for_reasoning(text, kind):
    assert _read(text)[0] == kind


@pytest.mark.parametrize("text", [
    "Hi there! Nothing running, what do you need?",
    "I understand your request completely.",
    "We need more milk, by the way.",
    "I will let you know when it's done.",
])
def test_plain_talk_with_no_marker_is_not_reasoning(text):
    """`_is_reasoning` is only ever reached by `_read` for text with NO
    marker in it - a marker-carrying reply is already handled and
    returned before this check runs."""
    assert not _is_reasoning(text)


def test_a_greeting_mentioning_the_user_in_passing_is_still_a_greeting():
    """"the user" appearing is not by itself reasoning - only the
    stock self-talk openings are."""
    assert not _is_reasoning("Sure, I'll let the user know shortly.")


def test_empty_text_is_not_reasoning():
    assert not _is_reasoning("")
