from src.brain.agent import Step
from src.brain.app_memory import AppMemory, app_key


def _mem(tmp_path):
    return AppMemory(tmp_path / "apps.sqlite3")


def _step(tool, args, ok=True, verdict="auto", result=None):
    return Step(1, "", tool, args, verdict, result or {"status": "success"}, ok)


# ---------------------------------------------------------------- keys


def test_app_key_normalises():
    assert app_key("Spotify") == "spotify"
    assert app_key("Spotify.exe") == "spotify"
    assert app_key("Spotify Premium") == "spotify"
    assert app_key("  SPOTIFY  ") == "spotify"


def test_app_key_takes_the_app_out_of_a_document_title():
    # "<document> - <app>" is the classic Windows title shape.
    assert app_key("Untitled - Notepad") == "notepad"
    assert app_key("report.docx - Word") == "word"


def test_app_key_handles_junk():
    assert app_key("") == ""
    assert app_key("   ") == ""


# ------------------------------------------------------------ recording


def test_records_opens_and_window_title(tmp_path):
    m = _mem(tmp_path)
    m.note_open("Spotify", "Spotify Premium")
    m.note_open("spotify")
    data = m.app("Spotify")
    assert data["opens"] == 2
    # a later open with no title must not wipe the known one
    assert data["window_title"] == "Spotify Premium"
    m.close()


def test_records_controls_with_use_counts(tmp_path):
    m = _mem(tmp_path)
    m.note_control("Spotify", "Search", "type", "Edit")
    m.note_control("Spotify", "Search", "type", "Edit")
    m.note_control("Spotify", "Play", "click", "Button")
    ctrls = {c["name"]: c for c in m.app("Spotify")["controls"]}
    assert ctrls["Search"]["uses"] == 2
    assert ctrls["Play"]["action"] == "click"
    m.close()


def test_notes_are_deduped(tmp_path):
    m = _mem(tmp_path)
    m.note("Spotify", "Ctrl+L focuses the search box")
    m.note("Spotify", "Ctrl+L focuses the search box")
    notes = m.app("Spotify")["notes"]
    assert len(notes) == 1 and notes[0]["uses"] == 2
    m.close()


def test_short_notes_are_ignored(tmp_path):
    m = _mem(tmp_path)
    m.note("Spotify", "hm")
    assert m.app("Spotify") is None
    m.close()


# ------------------------------------------------------ learning from steps


def test_learns_from_a_successful_task_trace(tmp_path):
    m = _mem(tmp_path)
    steps = [
        _step("open_app", {"app": "Spotify"},
              result={"status": "success", "window_title": "Spotify Premium"}),
        _step("ui_control", {"action": "tree", "window": "Spotify"}),
        _step("ui_control", {"action": "type", "window": "Spotify",
                             "name": "Search", "text": "drake"}),
        _step("ui_control", {"action": "click", "window": "Spotify",
                             "name": "Play"}),
    ]
    assert m.learn_from_steps(steps) >= 3

    data = m.app("Spotify")
    assert data["window_title"] == "Spotify Premium"
    names = {c["name"] for c in data["controls"]}
    assert {"Search", "Play"} <= names
    m.close()


def test_does_not_learn_from_failed_or_refused_steps(tmp_path):
    m = _mem(tmp_path)
    steps = [
        _step("ui_control", {"action": "click", "window": "Spotify",
                             "name": "Ghost"}, ok=False),
        _step("ui_control", {"action": "click", "window": "Spotify",
                             "name": "Nope"}, verdict="forbid"),
    ]
    assert m.learn_from_steps(steps) == 0
    assert m.app("Spotify") is None
    m.close()


def test_control_without_a_window_falls_back_to_the_opened_app(tmp_path):
    m = _mem(tmp_path)
    steps = [
        _step("open_app", {"app": "Notepad"}),
        _step("ui_control", {"action": "type", "name": "Text Editor",
                             "text": "hi"}),
    ]
    m.learn_from_steps(steps)
    names = {c["name"] for c in m.app("Notepad")["controls"]}
    assert "Text Editor" in names
    m.close()


# ---------------------------------------------------------------- profile


def test_profile_is_a_compact_prompt_block(tmp_path):
    m = _mem(tmp_path)
    m.note_open("Spotify", "Spotify Premium")
    m.note_control("Spotify", "Search", "type", "Edit")
    m.note_control("Spotify", "Play", "click", "Button")
    m.note("Spotify", "the search box needs a click before typing")

    text = m.profile("spotify")
    assert "Spotify Premium" in text
    assert "Search [Edit]" in text
    assert "click: Play [Button]" in text
    assert "needs a click before typing" in text
    m.close()


def test_profile_is_empty_for_unknown_apps(tmp_path):
    m = _mem(tmp_path)
    assert m.profile("emacs") == ""
    m.close()


def test_profiles_for_matches_apps_named_in_a_goal(tmp_path):
    m = _mem(tmp_path)
    m.note_open("Spotify", "Spotify Premium")
    m.note_control("Spotify", "Play", "click")
    m.note_open("Notepad")
    m.note_control("Notepad", "Text Editor", "type")

    both = m.profiles_for("open spotify and play a song, then note it in notepad")
    assert "Spotify" in both and "Notepad" in both

    one = m.profiles_for("play something on spotify")
    assert "Spotify" in one and "Notepad" not in one

    assert m.profiles_for("what is my ip address") == ""
    m.close()


def test_profiles_for_does_not_match_substrings_of_other_words(tmp_path):
    m = _mem(tmp_path)
    m.note_open("Word")
    m.note_control("Word", "Bold", "click")
    # "Word" must not match inside "wordpress" or "keyword"
    assert m.profiles_for("search for the keyword in my notes") == ""
    m.close()


def test_learns_the_window_title_from_a_ui_control_result(tmp_path):
    """The executor often addresses controls by ref, not name - Alfred must
    still learn the app's real window title from that."""
    m = _mem(tmp_path)
    steps = [
        _step("ui_control", {"action": "tree", "window": "notepad"},
              result={"status": "success", "window": "Untitled - Notepad"}),
        _step("ui_control", {"action": "type", "window": "notepad",
                             "into": 2, "text": "hi"}),
    ]
    assert m.learn_from_steps(steps) >= 1
    assert m.app("notepad")["window_title"] == "Untitled - Notepad"
    m.close()


# ------------------------------------------ controls with no name


def test_a_landmark_is_remembered_as_a_place_in_the_window(tmp_path):
    """MultiMC's Launch button has no name at any depth - only a
    position. Stored as a fraction so it survives the window moving."""
    m = _mem(tmp_path)
    m.note_landmark("MultiMC", "Launch", 0.904, 0.197)

    found = m.find_landmark("MultiMC", "Launch")
    assert found["rel_x"] == 0.904 and found["rel_y"] == 0.197
    m.close()


def test_an_exact_label_beats_a_longer_one(tmp_path):
    m = _mem(tmp_path)
    m.note_landmark("MultiMC", "Launch", 0.9, 0.197)
    m.note_landmark("MultiMC", "Launch Offline", 0.9, 0.215)

    assert m.find_landmark("MultiMC", "Launch")["label"] == "Launch"
    assert m.find_landmark("MultiMC", "offline")["label"] == "Launch Offline"
    m.close()


def test_relearning_moves_the_landmark_rather_than_duplicating_it(tmp_path):
    m = _mem(tmp_path)
    m.note_landmark("MultiMC", "Launch", 0.9, 0.19)
    m.note_landmark("MultiMC", "Launch", 0.9, 0.42)

    assert len(m.landmarks("MultiMC")) == 1
    assert m.find_landmark("MultiMC", "Launch")["rel_y"] == 0.42
    m.close()


def test_a_position_outside_the_window_is_refused(tmp_path):
    """A landmark off the window would click who knows what."""
    m = _mem(tmp_path)
    m.note_landmark("MultiMC", "Nowhere", 1.4, -0.2)

    assert m.landmarks("MultiMC") == []
    m.close()


def test_an_unknown_label_finds_nothing(tmp_path):
    m = _mem(tmp_path)
    m.note_landmark("MultiMC", "Launch", 0.9, 0.19)

    assert m.find_landmark("MultiMC", "banana") is None
    assert m.find_landmark("Steam", "Launch") is None
    m.close()


def test_landmarks_come_back_in_screen_order(tmp_path):
    m = _mem(tmp_path)
    m.note_landmark("MultiMC", "Delete", 0.9, 0.42)
    m.note_landmark("MultiMC", "Launch", 0.9, 0.19)

    assert [x["label"] for x in m.landmarks("MultiMC")] == ["Launch", "Delete"]
    m.close()


def test_learned_buttons_appear_in_the_profile(tmp_path):
    """The planner has no other way to know these names exist - they are
    not in the app's tree, only in what was learned about it."""
    m = _mem(tmp_path)
    m.note_open("MultiMC")
    m.note_landmark("MultiMC", "Launch", 0.9, 0.19)
    m.note_landmark("MultiMC", "Delete", 0.9, 0.42)

    text = m.profile("MultiMC")
    assert "Launch" in text and "Delete" in text
    assert "no name in the app" in text
    m.close()


def test_an_app_with_nothing_learned_says_nothing_about_it(tmp_path):
    m = _mem(tmp_path)
    m.note_open("Notepad")

    assert "no name in the app" not in m.profile("Notepad")
    m.close()


def test_an_app_can_hold_enough_notes_to_describe_itself(tmp_path):
    """Six was not enough for an app with a toolbar, a panel and a
    sub-window."""
    m = _mem(tmp_path)
    for i in range(14):
        m.note("MultiMC", f"a distinct thing worth remembering number {i}")

    assert len(m.app("MultiMC")["notes"]) >= 12
    m.close()


def test_known_apps_ranks_by_what_is_known_not_by_visits(tmp_path):
    """The two apps mapped most thoroughly had never been opened THROUGH
    Alfred, so ordering by visits left them off a line that claims to
    list what it knows its way around."""
    m = _mem(tmp_path)

    m.note_open("Notepad")
    m.note_open("Notepad")
    m.note_open("Notepad")

    m.note_open("MultiMC")          # opened once, but deeply mapped
    for i in range(6):
        m.note_landmark("MultiMC", f"Button {i}", 0.9, 0.1 + i / 20)
    m.note("MultiMC", "a genuinely useful thing about this app")

    order = m.known_apps()
    assert order.index("multimc") < order.index("notepad")
    m.close()


def test_an_app_with_nothing_known_still_appears(tmp_path):
    m = _mem(tmp_path)
    m.note_open("Calculator")

    assert "calculator" in m.known_apps()
    m.close()
