"""One Google account, several services, and the lines between them.

Gmail arrived first carrying its own sign-in. Adding the calendar that
way would have meant a second browser consent for the same account, and
Classroom a third - so the sign-in moved here and the services borrow
it.

The interesting part is what is refused, and by whom. Gmail's "never
send" is kept by Google: the permission Alfred holds has no send in it.
Calendar's "never delete" is kept by this code, because Google has no
permission that grants adding an event without also granting removing
one. Two different strengths of promise, and the tests say which is
which.
"""

from datetime import datetime, timedelta

from src.tools.calendar_tool import CalendarTool
from src.tools.classroom_tool import ClassroomTool
from src.workspace.account import CALENDAR, CLASSROOM, GMAIL, SCOPES

NOW = datetime(2026, 9, 1, 14, 30)


# ------------------------------------------------- what is asked for


def test_the_mail_permission_cannot_send():
    """The whole safety argument for mail rests on this."""
    assert GMAIL == ["https://www.googleapis.com/auth/gmail.modify"]
    assert not any("send" in s or "compose" in s for s in GMAIL)


def test_every_classroom_permission_is_read_only():
    """Alfred holds nothing that could submit work or change a grade."""
    assert CLASSROOM
    assert all(s.endswith(".readonly") for s in CLASSROOM), CLASSROOM


def test_the_calendar_permission_is_the_honest_exception():
    """There is no scope for "add events but never delete them", so
    that limit lives in the tool. Worth a test that says so out loud,
    because it is the one promise Google is not keeping for us."""
    assert CALENDAR == ["https://www.googleapis.com/auth/calendar.events"]


def test_one_sign_in_covers_all_of_them():
    assert set(SCOPES) == set(GMAIL) | set(CALENDAR) | set(CLASSROOM)


# ------------------------------------------------------ the calendar


class _Diary:
    def __init__(self):
        self.added = []

    def agenda(self, days=1, now=None, limit=20):
        return [{"title": "Dentist", "starts": "Tue 01 Sep 15:00"}]

    def next_up(self, now=None):
        return {"title": "Dentist", "starts": "Tue 01 Sep 15:00"}

    def free_between(self, start, end):
        return False, [{"title": "Dentist", "starts": "Tue 01 Sep 15:00"}]

    def add(self, title, start, minutes=60, where="", notes=""):
        self.added.append((title, start, minutes))
        return {"id": "e1", "title": title, "starts": str(start), "link": ""}


def _calendar():
    diary = _Diary()
    return CalendarTool(diary, now=lambda: NOW), diary


def test_it_can_say_what_is_on():
    tool, _ = _calendar()

    assert tool.execute({"action": "agenda"})["events"][0]["title"] == "Dentist"


def test_it_can_put_something_in_the_diary():
    tool, diary = _calendar()

    answer = tool.execute({
        "action": "add", "title": "Call the bank", "when": "tomorrow at 10am",
    })

    assert answer["status"] == "success"
    assert diary.added[0][0] == "Call the bank"
    assert diary.added[0][1].hour == 10


def test_it_will_not_delete_or_move_anything():
    tool, diary = _calendar()

    for action in ("delete", "remove", "cancel", "move", "edit", "update"):
        answer = tool.execute({"action": action, "id": "e1"})
        assert answer["status"] == "refused", action
        assert "let them do it" in answer["error"]
    assert diary.added == []


def test_adding_without_a_time_asks_for_one_in_plain_words():
    tool, _ = _calendar()

    answer = tool.execute({"action": "add", "title": "Something"})

    assert answer["status"] == "error"
    assert "tomorrow at 3" in answer["error"]


def test_it_can_check_whether_a_window_is_clear():
    tool, _ = _calendar()

    answer = tool.execute({"action": "free", "when": "tomorrow at 3pm"})

    assert answer["free"] is False
    assert answer["clashes"][0]["title"] == "Dentist"


# ----------------------------------------------------- the classroom


class _Classroom:
    def courses(self, active_only=True):
        return [{"id": "c1", "name": "Physics", "section": "", "teacher": ""}]

    def due(self, days=14, now=None):
        return [
            {"course": "Physics", "title": "Problem set 3",
             "due": "Fri 04 Sep 23:59", "due_iso": "2026-09-04T23:59",
             "overdue": False, "handed_in": False, "link": ""},
            {"course": "Physics", "title": "Reading",
             "due": "Wed 02 Sep 23:59", "due_iso": "2026-09-02T23:59",
             "overdue": False, "handed_in": True, "link": ""},
        ]

    def announcements(self, limit=10):
        return [{"course": "Physics", "text": "No lesson Friday",
                 "when": "2026-09-01 09:00"}]


def test_what_is_due_leaves_out_what_is_already_done():
    """Listing handed-in work alongside outstanding work makes a
    timetable look worse than it is."""
    tool = ClassroomTool(_Classroom())

    answer = tool.execute({"action": "due"})

    assert answer["count"] == 1
    assert answer["due"][0]["title"] == "Problem set 3"
    assert answer["already_handed_in"] == 1


def test_it_will_not_hand_work_in():
    tool = ClassroomTool(_Classroom())

    for action in ("submit", "turn_in", "hand_in", "join", "grade"):
        answer = tool.execute({"action": action})
        assert answer["status"] == "refused", action
        assert "user's own to do" in answer["error"]


def test_it_can_list_the_courses():
    tool = ClassroomTool(_Classroom())

    assert tool.execute({"action": "courses"})["courses"][0]["name"] == "Physics"


# --------------------------------- noticing a sign-in that is too old


def _account(tmp_path, held):
    import json

    from src.workspace.account import GoogleAccount

    token = tmp_path / "token.json"
    token.write_text(json.dumps({
        "token": "x", "refresh_token": "y", "scopes": held,
        "client_id": "c", "client_secret": "s",
    }), encoding="utf-8")
    return GoogleAccount(tmp_path / "secrets.json", token)


def test_a_sign_in_that_predates_a_new_service_is_spotted(tmp_path):
    """Adding a service after somebody has signed in is the normal way
    this happens, and the symptom without the check is an unreadable
    403 from whichever call needed the permission."""
    account = _account(tmp_path, GMAIL)

    missing = account.missing()

    assert set(missing) == set(CALENDAR) | set(CLASSROOM)


def test_a_full_sign_in_is_missing_nothing(tmp_path):
    assert _account(tmp_path, SCOPES).missing() == []


def test_it_says_to_sign_in_again_rather_than_failing_obscurely(tmp_path):
    from src.workspace.account import GoogleError

    account = _account(tmp_path, GMAIL)

    try:
        account.service("calendar", "v3")
    except GoogleError as exc:
        assert "link" in str(exc)
    else:
        raise AssertionError("should have refused")


def test_nothing_linked_at_all_is_a_different_message(tmp_path):
    from src.workspace.account import GoogleAccount, GoogleError

    account = GoogleAccount(tmp_path / "s.json", tmp_path / "nope.json")

    try:
        account.service("gmail", "v1")
    except GoogleError as exc:
        assert "no Google account linked" in str(exc)
    else:
        raise AssertionError("should have refused")
