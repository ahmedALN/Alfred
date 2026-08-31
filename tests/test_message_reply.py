"""Reading a message before acting on it.

Written after "Hello alfred" was taken as a job and Alfred opened
Notepad and typed Hello into it.
"""

from src.messaging.reply import Conversation, _read


class _Chat:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def generate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else "SAY: ok"


def _talk(*answers):
    jobs = []
    chat = _Chat(*answers)
    return Conversation(chat, jobs.append), jobs, chat


# --------------------------------------------------------- reading it


def test_a_greeting_is_answered_not_performed():
    """The one that opened Notepad."""
    talk, jobs, _ = _talk("SAY: Evening. What do you need?")

    assert talk.handle("Hello alfred") == "Evening. What do you need?"
    assert jobs == []


def test_real_work_still_gets_done():
    talk, jobs, _ = _talk("DO: Open Steam.")

    assert talk.handle("open steam") == "On it."
    assert jobs == ["Open Steam."]


def test_the_job_keeps_the_details_it_was_given():
    talk, jobs, _ = _talk("DO: Open MultiMC and launch the 1.21.11 instance.")
    talk.handle("open multimc and launch 1.21.11")

    assert "1.21.11" in jobs[0]


# ----------------------------------------------- when it cannot tell


def test_anything_it_cannot_read_is_treated_as_talk():
    """A greeting mistaken for a job types into Notepad. A job mistaken
    for a greeting costs one more message."""
    talk, jobs, _ = _talk("I think they said hello")

    assert talk.handle("hello") == "I think they said hello"
    assert jobs == []


def test_an_empty_answer_still_says_something():
    talk, jobs, _ = _talk("")

    assert talk.handle("hi") != ""
    assert jobs == []


def test_a_model_that_falls_over_still_gets_a_reply_out():
    """Silence is the one unacceptable answer - it is exactly what the
    bug looked like from the phone."""

    class _Broken:
        def generate(self, *a, **k):
            raise RuntimeError("no route to host")

    talk = Conversation(_Broken(), lambda job: None)

    assert talk.handle("you there?")
    assert "trouble" in talk.handle("you there?").lower()


def test_a_job_that_will_not_start_says_so():
    def _refuse(job):
        raise RuntimeError("queue is full")

    talk = Conversation(_Chat("DO: Open Steam."), _refuse)

    assert "queue is full" in talk.handle("open steam")


def test_nothing_is_said_about_an_empty_message():
    talk, jobs, chat = _talk()

    assert talk.handle("   ") == ""
    assert chat.prompts == []


# ------------------------------------------------------ remembering


def test_it_knows_what_was_just_said():
    talk, _, chat = _talk("SAY: Evening.", "SAY: Nothing yet.")
    talk.handle("hello")
    talk.handle("anything happening?")

    assert "Them: hello" in chat.prompts[1]
    assert "You: Evening." in chat.prompts[1]


def test_the_first_message_carries_no_history():
    talk, _, chat = _talk("SAY: Evening.")
    talk.handle("hello")

    assert "Recently" not in chat.prompts[0]


# ---------------------------------------------------------- parsing


def test_the_marker_is_found_even_when_the_model_waffles_around_it():
    assert _read("Sure thing! DO: Open Steam.") == ("do", "Open Steam.")


def test_only_the_first_line_of_an_essay_is_used():
    kind, body = _read("SAY: Here.\nAlso I could open Steam if you like.")

    assert (kind, body) == ("say", "Here.")


def test_a_bare_sentence_is_talk():
    assert _read("Evening, sir.") == ("say", "Evening, sir.")


def test_a_model_that_echoes_the_marker_back_is_understood():
    """Seen from the real one: "SAY: SAY" instead of an answer."""
    assert _read("SAY: SAY: Here.") == ("say", "Here.")
    assert _read("DO: DO: Open Steam.") == ("do", "Open Steam.")
