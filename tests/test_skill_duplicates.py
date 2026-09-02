"""A routine learned twice is a routine trusted half as much.

Thirty-nine skills were learned and four of them ever ran more than
once. Some of that is a young library. Some of it is that a skill is
named from the words of the request, so the same routine banked from
two phrasings becomes two rows - and each row then has to earn
confidence from scratch. `how-long-has-pc` was in the store twice
outright.

The rule is deliberately strict: same tools, same argument names, and a
request that reads recognisably the same. Two routines that merely have
the same purpose but reach it different ways are two routines, and
folding them would be throwing away the one that works.
"""

from __future__ import annotations

import pytest

from src.brain.skill_store import SkillStore
from src.brain.skills import SkillLibrary, _fingerprint


@pytest.fixture()
def library(tmp_path):
    store = SkillStore(tmp_path / "skills.sqlite3")
    yield SkillLibrary(store), store
    store.close()


def skill(skill_id, name, keywords, steps, **extra):
    base = {
        "id": skill_id,
        "name": name,
        "template": name.replace("-", " "),
        "keywords": list(keywords),
        "params": [],
        "steps": list(steps),
        "verify": "",
        "app": "",
        "tier": "ordinary",
        "danger_note": "",
        "success": 1,
        "fail": 0,
        "confidence": 0.55,
        "unconfirmed": False,
        "disabled": False,
    }
    base.update(extra)
    return base


UPTIME = [{"tool": "system_info", "args": {"query": "uptime"}}]
PORTS = [{"tool": "network_info", "args": {"query": "ports"}}]


# ====================================================================
# The fingerprint
# ====================================================================


def test_two_routines_running_the_same_calls_have_the_same_fingerprint():
    a = skill("a", "how-long-has-pc", ["how", "long", "pc"], UPTIME)
    b = skill("b", "how-long-has-pc", ["how", "long", "pc", "been"], UPTIME)

    assert _fingerprint(a) == _fingerprint(b)


def test_the_values_in_the_arguments_do_not_change_the_fingerprint():
    """`play a {p0} song` is one routine whichever artist taught it."""
    a = skill("a", "play", ["play"],
              [{"tool": "ui_control", "args": {"action": "type", "text": "drake"}}])
    b = skill("b", "play", ["play"],
              [{"tool": "ui_control", "args": {"action": "type", "text": "adele"}}])

    assert _fingerprint(a) == _fingerprint(b)


def test_different_tools_are_different_routines():
    a = skill("a", "check-steam", ["check", "steam", "running"],
              [{"tool": "powershell", "args": {"command": "Get-Process steam"}}])
    b = skill("b", "is-steam-running", ["is", "steam", "running"], UPTIME)

    assert _fingerprint(a) != _fingerprint(b)


def test_a_malformed_step_has_no_fingerprint_rather_than_a_wrong_one():
    assert _fingerprint(skill("a", "x", ["x"], [{"args": {}}])) == ()
    assert _fingerprint(skill("a", "x", ["x"], ["not a dict"])) == ()


# ====================================================================
# Saving
# ====================================================================


def test_saving_the_same_routine_twice_keeps_one_row(library):
    lib, store = library
    lib.save(skill("a", "how-long-has-pc", ["how", "long", "pc", "been"], UPTIME))
    lib.save(skill("b", "how-long-has-pc", ["how", "long", "pc", "been"], UPTIME))

    assert len(store.all(include_disabled=True)) == 1


def test_the_second_sighting_makes_the_first_more_trusted(library):
    """Two rows at one success each are worth less than one row at two."""
    lib, store = library
    lib.save(skill("a", "how-long-has-pc", ["how", "long", "pc", "been"],
                   UPTIME, confidence=0.55))
    lib.save(skill("b", "how-long-has-pc", ["how", "long", "pc", "been"], UPTIME))

    kept = store.all(include_disabled=True)[0]
    assert kept["success"] == 2
    assert kept["confidence"] > 0.55


def test_the_new_phrasing_is_folded_into_the_keywords(library):
    """So the next way of asking finds it too."""
    lib, store = library
    lib.save(skill("a", "how-long-has-pc", ["how", "long", "pc"], UPTIME))
    lib.save(skill("b", "uptime-since-reboot",
                   ["how", "long", "pc", "since", "reboot"], UPTIME))

    kept = store.all(include_disabled=True)[0]
    assert {"since", "reboot"} <= set(kept["keywords"])


def test_a_proven_run_confirms_a_routine_that_was_only_designed(library):
    lib, store = library
    lib.save(skill("a", "check-ports", ["check", "ports", "open"], PORTS,
                   unconfirmed=True, confidence=0.35))
    lib.save(skill("b", "check-ports", ["check", "ports", "open"], PORTS,
                   unconfirmed=False))

    assert store.all(include_disabled=True)[0]["unconfirmed"] is False


def test_a_genuinely_new_routine_is_saved(library):
    lib, store = library
    lib.save(skill("a", "how-long-has-pc", ["how", "long", "pc"], UPTIME))
    lib.save(skill("b", "which-ports-open", ["which", "ports", "open"], PORTS))

    assert len(store.all(include_disabled=True)) == 2


def test_two_routines_with_the_same_purpose_but_different_steps_both_stay(library):
    """`check-whether-steam-is` used powershell; `is-steam-running` used
    system_info. Folding them would throw away the one that works."""
    lib, store = library
    lib.save(skill("a", "check-whether-steam-is", ["check", "steam", "running"],
                   [{"tool": "powershell", "args": {"command": "x"}}]))
    lib.save(skill("b", "is-steam-running", ["is", "steam", "running"],
                   [{"tool": "system_info", "args": {"query": "processes"}}]))

    assert len(store.all(include_disabled=True)) == 2


def test_two_one_call_routines_for_unrelated_things_are_not_merged(library):
    """A shared shape is not a shared purpose."""
    lib, store = library
    lib.save(skill("a", "open-notepad", ["open", "notepad"],
                   [{"tool": "open_app", "args": {"app": "Notepad"}}]))
    lib.save(skill("b", "open-spotify", ["open", "spotify"],
                   [{"tool": "open_app", "args": {"app": "Spotify"}}]))

    assert len(store.all(include_disabled=True)) == 2


# ====================================================================
# Folding what is already there
# ====================================================================


def test_pruning_folds_duplicates_already_in_the_store(library):
    lib, store = library
    store.upsert(skill("a", "how-long-has-pc", ["how", "long", "pc", "been"],
                       UPTIME, success=3))
    store.upsert(skill("b", "how-long-has-pc", ["how", "long", "pc", "been"],
                       UPTIME, success=1))

    folded = lib.prune_duplicates()

    assert len(folded) == 1
    assert len(store.all(include_disabled=True)) == 1


def test_pruning_keeps_the_row_that_has_actually_worked(library):
    lib, store = library
    store.upsert(skill("weak", "how-long-has-pc", ["how", "long", "pc"],
                       UPTIME, success=1, confidence=0.4))
    store.upsert(skill("strong", "how-long-has-pc", ["how", "long", "pc"],
                       UPTIME, success=5, confidence=0.9))

    lib.prune_duplicates()
    kept = store.all(include_disabled=True)

    assert len(kept) == 1
    assert kept[0]["id"] == "strong"


def test_a_dry_run_changes_nothing(library):
    lib, store = library
    store.upsert(skill("a", "how-long-has-pc", ["how", "long", "pc"], UPTIME))
    store.upsert(skill("b", "how-long-has-pc", ["how", "long", "pc"], UPTIME))

    found = lib.find_duplicates()

    assert len(found) == 1
    assert len(store.all(include_disabled=True)) == 2


def test_pruning_a_clean_library_does_nothing(library):
    lib, store = library
    store.upsert(skill("a", "how-long-has-pc", ["how", "long", "pc"], UPTIME))
    store.upsert(skill("b", "which-ports", ["which", "ports"], PORTS))

    assert lib.prune_duplicates() == []
    assert len(store.all(include_disabled=True)) == 2


def test_pruning_an_empty_library_does_not_fall_over(library):
    lib, _store = library
    assert lib.prune_duplicates() == []


# ====================================================================
# A routine is learned from a request, not from thinking out loud
# ====================================================================


def test_thinking_out_loud_is_not_a_request():
    """`screenshot-request-we-should` was really in the library.

    Its template was four hundred characters of a reasoning model
    working out which reply format to use - "we should use SHOW:
    picture. But we also need to..." - saved as the request to match
    future requests against. Nothing would ever match it; it just made
    the list longer and the confidence numbers worse.
    """
    from src.brain.skills import is_a_request

    assert not is_a_request(
        " For screenshot request, we should use SHOW: picture. But we "
        "also need to bring Claude to foreground before taking a "
        "screenshot. The format expects one line."
    )
    assert not is_a_request("Let me think. First I will open the app.")


@pytest.mark.parametrize("goal", [
    "Open Notepad.",
    'Open Steam and launch "Sons of the Forest" directly from the desktop file.',
    "Learn a routine for launching Steam before launching a game from the desktop.",
    "play a {p0} song on Spotify",
    "What is in my Stremio continue watching list?",
    "Open Stremio and open Breaking Bad from my continue watching list.",
    "How much free space is on the C drive?",
])
def test_a_real_request_is_still_learned_from(goal):
    from src.brain.skills import is_a_request

    assert is_a_request(goal)


@pytest.mark.parametrize("goal", [
    "",
    "   ",
    "x" * 200,
    "one thing\nand then another",
])
def test_nothing_shaped_like_a_note_to_self_is_learned_from(goal):
    from src.brain.skills import is_a_request

    assert not is_a_request(goal)


def test_distilling_refuses_a_goal_nobody_said(library):
    lib, _store = library

    assert lib.distill(
        " We should use SHOW: picture. But we also need to bring Claude "
        "to the foreground first. However the format expects one line.",
        [("ui_control", {"action": "click", "name": "Play"})],
    ) is None
