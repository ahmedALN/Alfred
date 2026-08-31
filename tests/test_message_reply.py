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
    talk, jobs, _ = _talk("DO: Open Steam. || Opening Steam now.")

    assert talk.handle("open steam") == "Opening Steam now."
    assert jobs == ["Open Steam."]


def test_the_job_keeps_the_details_it_was_given():
    talk, jobs, _ = _talk(
        "DO: Open MultiMC and launch the 1.21.11 instance. || "
        "Launching your 1.21.11 instance."
    )
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

    talk = Conversation(_Chat("DO: Open Steam. || Opening it."), _refuse)

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


# ------------------------------------- saying what it understood


def test_it_says_what_it_understood_not_just_that_it_heard():
    """"On it." tells you a message arrived. It does not tell you
    whether the right thing is about to happen - which is the one
    moment you could still correct it."""
    talk, _, _ = _talk(
        "DO: Open MultiMC and launch the 1.21.11 instance. || "
        "Launching your 1.21.11 instance."
    )

    said = talk.handle("open multimc and launch 1.21.11")

    assert said == "Launching your 1.21.11 instance."
    assert said != "On it."


def test_a_model_that_forgets_the_words_still_says_something_specific():
    talk, _, _ = _talk("DO: Open Steam and search for Hades.")

    said = talk.handle("find hades on steam")

    assert "Steam" in said and "Hades" in said
    assert said != "On it."


def test_the_job_is_not_polluted_by_the_words_said_back():
    talk, jobs, _ = _talk("DO: Open Steam. || Opening Steam now.")
    talk.handle("open steam")

    assert jobs == ["Open Steam."]


def test_the_separator_is_only_taken_when_it_is_there():
    from src.messaging.reply import _split

    assert _split("Open Steam.") == ("Open Steam.", "")
    assert _split("Open Steam. || Opening Steam.") == \
        ("Open Steam.", "Opening Steam.")


# ------------------------------------------- steering from the phone


def _steerable(*answers, running="Open Steam and search for Hades."):
    from src.messaging.reply import Conversation

    jobs, steers = [], []
    talk = Conversation(
        _Chat(*answers), jobs.append,
        steer=lambda text: (steers.append(text), True)[1],
        running=lambda: running,
    )
    return talk, jobs, steers


def test_a_correction_reaches_the_running_job():
    """Rather than starting a second job that fights the first."""
    talk, jobs, steers = _steerable("STEER: search for Hollow Knight instead")

    said = talk.handle("no not hades, hollow knight")

    assert steers == ["search for Hollow Knight instead"]
    assert jobs == []
    assert "Hollow Knight" in said


def test_the_model_is_told_what_is_running():
    """Whether anything is running decides whether steering is even on
    the table."""
    talk, _, _ = _steerable("SAY: ok")
    talk.handle("anything")

    assert "RUNNING NOW: Open Steam" in talk._chat.prompts[0]


def test_with_nothing_running_it_is_told_that_too():
    talk, _, _ = _steerable("SAY: ok", running="")
    talk.handle("anything")

    assert "STEER is not an option" in talk._chat.prompts[0]


def test_a_correction_with_nothing_to_correct_becomes_the_job():
    """Better to do the thing than explain a distinction nobody cares
    about."""
    from src.messaging.reply import Conversation

    jobs = []
    talk = Conversation(
        _Chat("STEER: open the 1.21.11 instance"), jobs.append,
        steer=lambda text: False,        # nothing running
        running=lambda: "",
    )

    said = talk.handle("make it the 1.21.11 one")

    assert jobs == ["open the 1.21.11 instance"]
    assert said


# ------------------------------------------- being shown something


class _Eyes:
    def __init__(self, says="A screenshot of a Python traceback."):
        self.says = says
        self.asked = []

    def analyze(self, data, prompt, *, mime_type="image/png"):
        self.asked.append((len(data), prompt, mime_type))
        return self.says


def _looker(eyes=None, **kw):
    from src.messaging.reply import Conversation

    jobs = []
    return Conversation(_Chat("SAY: should not be used"), jobs.append,
                        eyes=eyes or _Eyes(), **kw), jobs


def test_a_picture_is_answered_not_routed():
    """"What is this?" is not a job for the task agent."""
    talk, jobs = _looker()

    said = talk.handle("what is this?", media=b"JPEGDATA", kind="image")

    assert "traceback" in said
    assert jobs == []


def test_a_picture_with_no_caption_is_still_a_message():
    """It is the commonest way anybody shows anybody anything."""
    eyes = _Eyes()
    talk, _ = _looker(eyes)

    said = talk.handle("", media=b"JPEGDATA", kind="image")

    assert said
    assert "What is this" in eyes.asked[0][1]


def test_the_caption_is_the_question():
    eyes = _Eyes()
    talk, _ = _looker(eyes)

    talk.handle("is this safe to click?", media=b"JPEGDATA", kind="image")

    asked = eyes.asked[0][1]
    assert asked.startswith("is this safe to click?")
    # ...and the answer is going to a phone, so it is asked for short
    # and in plain text rather than six headings about a photo.
    assert "no markdown" in asked


def test_a_video_is_declined_in_a_useful_way():
    talk, _ = _looker()

    said = talk.handle("look at this", media=b"MP4DATA", kind="video")

    assert "screenshot" in said


def test_with_no_eyes_it_says_so_rather_than_pretending():
    from src.messaging.reply import Conversation

    talk = Conversation(_Chat("SAY: hi"), lambda job: None, eyes=None)

    assert "no way to look" in talk.handle("what is this", b"X", "image")


def test_a_broken_eye_does_not_swallow_the_message():
    class _Blind:
        def analyze(self, *a, **k):
            raise RuntimeError("quota")

    talk, _ = _looker(_Blind())

    assert "send it again" in talk.handle("what is this", b"X", "image")


def test_what_was_seen_is_remembered_for_the_next_message():
    """So "and the line above it?" means something."""
    eyes = _Eyes()
    talk, _ = _looker(eyes)

    talk.handle("what is this?", media=b"JPEGDATA", kind="image")

    assert any("[a picture]" in line for line in talk._history)
