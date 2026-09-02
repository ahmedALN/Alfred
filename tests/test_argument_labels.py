"""The label on an argument is not a reason to refuse a call.

Alfred's own limitation store is unambiguous about this. Of the
seventy-four things it has learned the hard way, sixty-two are a tool
refusing a call over the name of an argument:

    41x  open_app    'app' must be a non-empty string
     8x  skill       step 2 of 'ui_control' is missing ['action']
     6x  ui_control  action must be one of [...]
     2x  remember    'content' must be a string

Not one of them failed because Alfred could not do the thing. Each cost
a round trip to be told a synonym, and each also cost a fact written
into memory about Alfred's own schema - memory spent on nothing.

Every call in the first block below is one that really happened and was
really refused, taken from alfred_brain_audit.jsonl and
alfred_limitations.sqlite3. The second block is the other half of the
bargain: a call that genuinely says nothing must still be refused, not
guessed at.
"""

from __future__ import annotations

import pytest

from src.tools.arguments import (
    normalise_enum_action,
    normalise_named_string,
    normalise_open_app,
    normalise_ui_control,
    unwrap,
)
from src.tools.ui_control import _ACTIONS


def _remember(args):
    return normalise_named_string(
        args, "content",
        ("text", "fact", "value", "memory", "statement", "note"))


def _skill(args):
    return normalise_enum_action(
        args, ("learn", "list", "show", "forget"),
        (("learn", ("goal",)), ("show", ("name",))))


def _classroom(args):
    return normalise_enum_action(
        args, ("due", "courses", "announcements"), (("due", ("days",)),))


def _ui(args):
    return normalise_ui_control(args, _ACTIONS)


# ====================================================================
# Calls that really happened, and were really refused
# ====================================================================


@pytest.mark.parametrize("args,expected", [
    # 2026-08-31T14:27 - the name of the app, in `target`
    ({"target": "steam"}, "steam"),
    # 2026-09-02T05:27
    ({"target": "how to fish on the PC"}, "how to fish on the PC"),
    # 2026-09-02T12:23 - desktop in `target`, name in `text`
    ({"target": "current", "text": "How to Fix"}, "How to Fix"),
    # the four bench failures
    ({"target": "current", "query": "Notepad"}, "Notepad"),
    ({"name": "Spotify"}, "Spotify"),
    ({"application": "Chrome"}, "Chrome"),
    ({"program": "Calculator"}, "Calculator"),
    # wrapped in the shape of something else
    ({"app": {"name": "Notepad"}}, "Notepad"),
    ({"app": ["Notepad"]}, "Notepad"),
])
def test_open_app_reads_the_name_wherever_it_is(args, expected):
    assert normalise_open_app(dict(args))["app"] == expected


def test_open_app_keeps_a_real_desktop_word_as_a_desktop_word():
    out = normalise_open_app({"app": "Notepad", "target": "user"})
    assert out["app"] == "Notepad"
    assert out["target"] == "user"


def test_a_name_promoted_out_of_target_does_not_leave_a_bad_desktop_behind():
    """"steam" is not a desktop, and must not be left where one goes."""
    out = normalise_open_app({"target": "steam"})
    assert out["app"] == "steam"
    assert out.get("target") in (None, "alfred", "current", "user")


@pytest.mark.parametrize("args,expected", [
    ({"text": "The user hates coriander."}, "The user hates coriander."),
    ({"content": {"text": "Deji is a friend."}}, "Deji is a friend."),
    ({"fact": "The router is in the hall."}, "The router is in the hall."),
    ({"content": ["one thing"]}, "one thing"),
])
def test_remember_takes_the_fact_however_it_is_labelled(args, expected):
    assert _remember(dict(args))["content"] == expected


@pytest.mark.parametrize("args,expected", [
    ({"goal": "launch games from their desktop files"}, "learn"),
    ({"action": "Learn"}, "learn"),
    ({"op": "list"}, "list"),
    ({"action": "learn "}, "learn"),
    ({"name": "play-a-song"}, "show"),
])
def test_skill_works_out_which_action_was_meant(args, expected):
    assert _skill(dict(args))["action"] == expected


def test_classroom_defaults_to_what_is_due_when_days_is_given():
    assert _classroom({"days": 7})["action"] == "due"


@pytest.mark.parametrize("args,expected", [
    ({"window": "Notepad", "text": "hello"}, "type"),
    ({"path": "File->Save As"}, "menu"),
    ({"window": "Steam", "query": "Hades"}, "search"),
    ({"item": "1.21.11"}, "select"),
    ({"keys": "^s"}, "key"),
    ({"direction": "down"}, "scroll"),
    ({"window": "Notepad"}, "tree"),
    ({"ref": 4}, "click"),
    # a synonym for `action` itself
    ({"op": "tree", "app": "Notepad"}, "tree"),
    ({"operation": "focus", "window": "Steam"}, "focus"),
    # ...and a synonym for `window`
    ({"app": "Notepad", "text": "hi"}, "type"),
    ({"title": "Notepad", "text": "hi"}, "type"),
])
def test_ui_control_works_out_which_action_was_meant(args, expected):
    assert _ui(dict(args))["action"] == expected


def test_ui_control_moves_a_window_name_to_where_the_tool_looks_for_it():
    assert _ui({"app": "Notepad", "op": "tree"})["window"] == "Notepad"


def test_a_named_action_is_never_second_guessed():
    """An action nobody recognises is a mistake to report, not to paper over."""
    out = _ui({"action": "teleport", "window": "Notepad"})
    assert out["action"] == "teleport"


# ====================================================================
# The other half: an unclear call is still refused
# ====================================================================


@pytest.mark.parametrize("args", [
    {"target": "current"},
    {"target": "user"},
    {"target": "alfred"},
    {},
    {"app": ""},
    {"app": "   "},
])
def test_open_app_still_refuses_a_call_with_no_name_in_it(args):
    """A tool that does something plausible with an unclear instruction
    is worse than one that asks."""
    assert normalise_open_app(dict(args)).get("app") in (None, "", "   ")


def test_remember_still_refuses_a_call_with_nothing_to_remember():
    assert _remember({}).get("content") is None
    assert _remember({"category": "preference"}).get("content") is None


def test_an_action_is_not_invented_from_nothing():
    assert _ui({}).get("action") is None
    assert _skill({}).get("action") is None


def test_a_dict_with_several_keys_is_not_unwrapped_by_guessing():
    assert unwrap({"a": "one", "b": "two"}) == {"a": "one", "b": "two"}


def test_a_list_of_several_things_is_not_unwrapped():
    assert unwrap(["one", "two"]) == ["one", "two"]


# ====================================================================
# Wired all the way through the registry
# ====================================================================


def test_the_registry_tidies_arguments_before_the_tool_sees_them():
    """Every caller - voice, the task agent, a replayed skill, the brain -
    comes through this one door."""
    from src.tools.base import AlfredTool
    from src.tools.registry import ToolRegistry

    class Fussy(AlfredTool):
        name = "fussy"
        description = "wants exactly one thing, spelled its way"

        def __init__(self):
            self.seen = None

        @property
        def parameters_schema(self):
            return {"type": "object", "properties": {"app": {"type": "string"}}}

        def normalise_arguments(self, arguments):
            return normalise_open_app(arguments)

        def execute(self, arguments):
            self.seen = arguments
            return {"status": "success"}

    tool = Fussy()
    registry = ToolRegistry()
    registry.register(tool)
    registry.execute("fussy", {"target": "steam"})

    assert tool.seen["app"] == "steam"


def test_a_normaliser_that_throws_does_not_take_the_call_down_with_it():
    from src.tools.base import AlfredTool
    from src.tools.registry import ToolRegistry

    class Cursed(AlfredTool):
        name = "cursed"
        description = "its tidying is broken"

        @property
        def parameters_schema(self):
            return {"type": "object", "properties": {}}

        def normalise_arguments(self, arguments):
            raise RuntimeError("boom")

        def execute(self, arguments):
            return {"status": "success", "got": arguments}

    registry = ToolRegistry()
    registry.register(Cursed())

    assert registry.execute("cursed", {"x": 1}) == {
        "status": "success", "got": {"x": 1}
    }


def test_every_tool_can_be_asked_to_tidy_arguments():
    """The hook is on the base class, so no tool can be missed by it."""
    from src.tools.base import AlfredTool

    assert callable(AlfredTool.normalise_arguments)
    assert AlfredTool.normalise_arguments(None, {"a": 1}) == {"a": 1}


# ====================================================================
# A verb that is not ours, but plainly means one of ours
# ====================================================================


def test_play_this_item_is_open_item():
    """Live, driving Spotify:

        {"action": "play", "item": "Shababs by Drake"}

    There is no `play`, so the tool answered with its list of
    twenty-seven verbs, and Alfred told the user it could not play the
    song without mapping the app first - which was neither true nor the
    problem. "Play this item" is open_item and nothing else.
    """
    assert _ui({
        "action": "play", "item": "Shababs by Drake",
        "window": "Spotify Premium",
    })["action"] == "open_item"


@pytest.mark.parametrize("verb,means", [
    ("launch", "open_item"),
    ("start", "open_item"),
    ("open", "open_item"),
    ("press", "click"),
    ("tap", "click"),
    ("enter", "type"),
    ("write", "type"),
    ("choose", "select"),
    ("read", "get"),
    ("quit", "close"),
    ("dismiss", "clear_popups"),
])
def test_the_verbs_a_model_reaches_for(verb, means):
    """These are what it says when it is thinking about the app rather
    than about the tool, and every one cost a refusal and a turn."""
    out = _ui({"action": verb, "item": "a thing", "name": "a thing",
               "text": "a thing", "window": "An App"})

    assert out["action"] == means


def test_a_real_action_is_never_rewritten():
    for action in ("click", "type", "search", "tree", "open_item", "close"):
        assert _ui({"action": action, "window": "X"})["action"] == action


def test_an_unknown_verb_with_nothing_to_go_on_is_left_to_be_refused():
    """The other half of the bargain stays intact.

    It is kept, not blanked: the tool then answers "action must be one
    of [...]", which tells the model what went wrong. Quietly turning
    it into something plausible would not.
    """
    assert _ui({"action": "teleport"})["action"] == "teleport"
    assert _ui({"action": "teleport", "window": "Notepad"})["action"] == "teleport"


def test_an_unknown_verb_falls_through_to_what_the_call_implies():
    """`{"action": "frobnicate", "path": "File->Save"}` is a menu, and
    the unknown verb should not stop it being one."""
    assert _ui({"action": "frobnicate", "path": "File->Save"})["action"] == "menu"
