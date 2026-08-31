"""Checking with your eyes that the thing actually happened.

Verification read a tool log and asked a model whether the log looked
like success. That is a fair check of whether Alfred DID something and
no check at all of whether it WORKED - which is how a screenshot that
was never saved came to be reported as saved.

Alfred has had eyes throughout. It used them to read buttons nobody had
named, and never once to look at the screen and ask whether the last
thing it did had landed.
"""

from src.brain.looksright import contradicted, worth_looking


class _Eyes:
    def __init__(self, says):
        self.says = says
        self.asked = []

    def analyze(self, data, prompt, *, mime_type="image/png"):
        self.asked.append(prompt)
        return self.says


def _look(says, png=b"PNG"):
    eyes = _Eyes(says)
    return contradicted("Search Steam for Hades", "the store shows Hades",
                        lambda: png, eyes), eyes


# ------------------------------------------ when it is worth looking


def test_something_you_could_see_is_worth_a_look():
    assert worth_looking("the Steam window shows Hades") is True
    assert worth_looking("Notepad is open") is True


def test_something_a_tool_already_settled_is_not():
    """A file, a process, a port - the tool result that reported it is
    better evidence than a photograph of a desktop."""
    assert worth_looking("a file exists at C:/x.png") is False
    assert worth_looking("the process is running") is False
    assert worth_looking("system_info returns a FreeGB value") is False


def test_nothing_to_check_is_not_worth_a_look():
    assert worth_looking("") is False
    assert worth_looking("it worked") is False


# ------------------------------------------------ what it will say


def test_a_screen_that_shows_otherwise_is_a_contradiction():
    (denied, seen), _ = _look("NO - the store is showing Trombone Champ.")

    assert denied is True
    assert "Trombone" in seen


def test_a_screen_that_agrees_is_not_treated_as_proof():
    """A screen showing the thing does not mean the whole step is
    complete - it only means nothing contradicts it."""
    (denied, _), _ = _look("YES - Hades is on screen.")

    assert denied is False


def test_unsure_is_not_a_contradiction():
    """The point is catching a lie, not inventing one."""
    (denied, _), _ = _look("UNSURE - the window is too small to read.")

    assert denied is False


def test_it_is_told_that_unsure_is_usually_right():
    _, eyes = _look("UNSURE")

    assert "UNSURE is the right answer far more often" in eyes.asked[0]


# ------------------------------------------------- when it cannot look


def test_no_eyes_means_no_opinion():
    assert contradicted("s", "d", lambda: b"PNG", None) == (False, "")


def test_no_screenshot_means_no_opinion():
    assert contradicted("s", "d", None, _Eyes("NO")) == (False, "")


def test_a_blank_screen_grab_means_no_opinion():
    assert contradicted("s", "d", lambda: b"", _Eyes("NO")) == (False, "")


def test_a_camera_that_falls_over_does_not_fail_the_step():
    def _broken():
        raise RuntimeError("no session")

    assert contradicted("s", "d", _broken, _Eyes("NO")) == (False, "")


def test_an_eye_that_falls_over_does_not_fail_the_step():
    class _Blind:
        def analyze(self, *a, **k):
            raise RuntimeError("quota")

    assert contradicted("s", "d", lambda: b"PNG", _Blind()) == (False, "")


# -------------------------------------------------- through the agent


def test_the_agent_only_looks_at_claims_a_picture_could_disprove():
    from src.brain.agent import TaskAgent

    agent = TaskAgent.__new__(TaskAgent)
    agent._vision = _Eyes("NO - nothing like it on screen")
    agent._screenshot = lambda: b"PNG"

    invisible = agent._eyes_disagree({"step": "s", "done_when": "a file exists"})
    visible = agent._eyes_disagree({"step": "s", "done_when": "Notepad is open"})

    assert invisible == (False, "")
    assert visible[0] is True


def test_an_agent_with_no_eyes_carries_on_as_before():
    from src.brain.agent import TaskAgent

    agent = TaskAgent.__new__(TaskAgent)
    agent._vision = None
    agent._screenshot = None

    assert agent._eyes_disagree({"step": "s", "done_when": "Notepad is open"}) \
        == (False, "")
