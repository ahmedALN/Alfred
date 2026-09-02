"""Taking the call the model meant, when the label is the only thing wrong.

Alfred's own record says this plainly. Of the seventy-four limitations
it has learned the hard way, sixty-two are a tool refusing a call over
the name on an argument:

    41x  open_app    'app' must be a non-empty string
     8x  skill       step 2 of 'ui_control' is missing ['action']
     6x  ui_control  action must be one of [...]
     2x  remember    'content' must be a string

None of them failed because Alfred could not do the thing. Every one
cost a round trip to be told a synonym - which at several seconds a call
is most of what "slow" was made of, and every one of them also cost a
fact written into memory about Alfred's own schema, which is memory
spent on nothing.

The rule here is narrow on purpose. A value is moved to the argument it
was obviously meant for; a missing enum is filled in only when the other
arguments admit exactly one answer. Nothing is guessed. When a call is
genuinely ambiguous it is still refused, because a tool that does
something plausible with an unclear instruction is worse than one that
asks.
"""

from __future__ import annotations

from typing import Any

# Words that mean "which desktop", as opposed to "what to open". Anything
# else in `target` is the name of a thing.
DESKTOP_WORDS = frozenset({
    "user", "current", "alfred", "mine", "here", "own", "isolated",
    "child", "hidden", "same", "this",
})


def _first_text(arguments: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = arguments.get(key)

        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def unwrap(value: Any) -> Any:
    """A string that arrived wrapped in the shape of something else.

    Models pass `{"content": {"text": "..."}}` and `["..."]` for a
    plain string argument often enough that refusing them is a refusal
    over punctuation.
    """

    if isinstance(value, dict):
        for key in ("text", "content", "value", "name", "query", "string"):
            inner = value.get(key)
            if isinstance(inner, str) and inner.strip():
                return inner

        # A one-entry dict has only one thing it could have meant.
        if len(value) == 1:
            only = next(iter(value.values()))
            if isinstance(only, str) and only.strip():
                return only

    if isinstance(value, (list, tuple)) and len(value) == 1:
        return unwrap(value[0])

    return value


# --------------------------------------------------------------- open_app

_APP_SYNONYMS = (
    "app", "name", "application", "app_name", "query", "program",
    "executable", "path", "title", "item", "window", "text", "url",
    "site", "website", "file",
)


def normalise_open_app(arguments: dict[str, Any]) -> dict[str, Any]:
    """`{"target": "steam"}` means open Steam, not open the "steam" desktop.

    Every one of the forty-one refusals had a name in it. Five of them
    put it in `target`, which is the argument that says which desktop -
    so the tool read "steam" as a desktop, found no app, and refused a
    call that could not have been clearer.
    """

    out = dict(arguments)

    for key in ("app", "name", "target", "text", "query", "title"):
        if key in out:
            out[key] = unwrap(out[key])

    app = _first_text(out, ("app",))

    if app:
        return out

    target = out.get("target")

    # A target that is not a desktop word is the thing to open.
    if (
        isinstance(target, str)
        and target.strip()
        and target.strip().lower() not in DESKTOP_WORDS
    ):
        out["app"] = target.strip()
        out.pop("target", None)
        return out

    borrowed = _first_text(out, _APP_SYNONYMS)

    if borrowed and borrowed.lower() not in DESKTOP_WORDS:
        out["app"] = borrowed

    return out


# ------------------------------------------------------------- ui_control

# Which action an argument set can only have meant. Ordered: the first
# rule whose arguments are all present wins.
_UI_ACTION_TELLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("menu", ("path",)),
    ("key", ("keys",)),
    ("search", ("query", "window")),
    ("type", ("text",)),
    ("select", ("item",)),
    ("scroll", ("direction",)),
    ("learn_control", ("x", "y", "name")),
    ("click", ("ref",)),
    ("click", ("name",)),
    ("tree", ("window",)),
)

_UI_SYNONYMS: dict[str, tuple[str, ...]] = {
    "window": ("window", "app", "title", "application", "target", "in"),
    "name": ("name", "control", "button", "label", "element"),
    "text": ("text", "value", "input", "type_text"),
    "query": ("query", "search", "search_text", "contains", "term"),
    "item": ("item", "option", "choice", "entry", "row"),
    "path": ("path", "menu", "menu_path"),
    "keys": ("keys", "key", "keystroke", "shortcut"),
}

# `action` is also spelled these.
_ACTION_KEYS = ("action", "op", "operation", "command", "verb", "do")

# Verbs that are not ui_control actions but plainly mean one.
#
# These are what a model reaches for when it is thinking about the app
# rather than about the tool - "play this", "launch that" - and every
# one of them cost a refusal and a wasted turn.
_MEANS: dict[str, str] = {
    "play": "open_item",
    "launch": "open_item",
    "start": "open_item",
    "run": "open_item",
    "open": "open_item",
    "choose": "select",
    "pick": "select",
    "tap": "click",
    "press": "click",
    "push": "click",
    "hit": "click",
    "enter": "type",
    "write": "type",
    "input": "type",
    "fill": "type",
    "read": "get",
    "list": "tree",
    "inspect": "tree",
    "look": "tree",
    "wait": "wait_ready",
    "dismiss": "clear_popups",
    "quit": "close",
    "exit": "close",
}


def normalise_ui_control(
    arguments: dict[str, Any],
    valid_actions: tuple[str, ...] | frozenset[str] = (),
) -> dict[str, Any]:
    """Fill in `action` when the rest of the call admits one answer."""

    out = dict(arguments)

    for canonical, synonyms in _UI_SYNONYMS.items():
        if canonical in out and out[canonical] not in (None, ""):
            out[canonical] = unwrap(out[canonical])
            continue

        borrowed = _first_text(out, synonyms)

        if borrowed is not None:
            out[canonical] = borrowed

    action = _first_text(out, _ACTION_KEYS)

    if action:
        tidy = action.strip().lower().replace(" ", "_").replace("-", "_")
        out["action"] = tidy
        _keys_for_the_key_action(out)

        if not valid_actions or tidy in valid_actions:
            return out

        # A verb that is not one of ours, but plainly means one of them.
        #
        # Live, driving Spotify: {"action": "play", "item": "Shababs by
        # Drake"}. There is no `play`, so the tool answered with its
        # list of twenty-seven verbs, Alfred apologised to the user and
        # said it could not play the song without mapping the app -
        # which was not true and not the problem. "Play this item" is
        # open_item and nothing else.
        meant = _MEANS.get(tidy)

        if meant and (not valid_actions or meant in valid_actions):
            out["action"] = meant
            _keys_for_the_key_action(out)
            return out

        # Otherwise fall through: if the rest of the call points at
        # exactly one real action, take that.
        #
        # `specific_only` is what keeps "a named action nobody
        # recognises is a mistake to report" true. The last tell -
        # a window and nothing else means `tree` - is a sensible
        # default for a call with no verb at all, and much too eager
        # for one whose verb we simply did not know: it would turn
        # {"action": "obliterate", "window": "X"} into a quiet tree
        # read instead of saying the verb does not exist.
        return _from_the_tells(out, valid_actions, specific_only=True)

    return _from_the_tells(out, valid_actions, specific_only=False)


def _from_the_tells(
    out: dict[str, Any],
    valid_actions: tuple[str, ...] | frozenset[str],
    *,
    specific_only: bool,
) -> dict[str, Any]:
    for candidate, required in _UI_ACTION_TELLS:
        if valid_actions and candidate not in valid_actions:
            continue

        if specific_only and required == ("window",):
            continue

        if all(
            out.get(field) not in (None, "", [])
            for field in required
        ):
            out["action"] = candidate
            _keys_for_the_key_action(out)
            return out

    return out


def _keys_for_the_key_action(out: dict[str, Any]) -> None:
    """`{"action": "key", "name": "Return"}` means press Return.

    `name` is the control to act on for every other action, so it
    cannot be a general synonym for `keys` - but a `key` press has no
    control, and the only thing a name could be naming there is the
    key. Seen live driving Stremio: the call was refused, the step was
    retried, and the retry cost more than the press would have.
    """

    if str(out.get("action") or "").lower() != "key":
        return

    if str(out.get("keys") or "").strip():
        return

    for source in ("name", "item", "text", "query"):
        borrowed = out.get(source)

        if isinstance(borrowed, str) and borrowed.strip():
            out["keys"] = borrowed.strip()
            out.pop(source, None)
            return


# ------------------------------------------------------- single-string tools


def normalise_named_string(
    arguments: dict[str, Any],
    canonical: str,
    synonyms: tuple[str, ...],
) -> dict[str, Any]:
    """One required string argument, under whichever name it arrived."""

    out = dict(arguments)

    if canonical in out:
        out[canonical] = unwrap(out[canonical])

    value = out.get(canonical)

    if isinstance(value, str) and value.strip():
        return out

    for key in synonyms:
        borrowed = unwrap(out.get(key))

        if isinstance(borrowed, str) and borrowed.strip():
            out[canonical] = borrowed.strip()
            return out

    return out


def normalise_enum_action(
    arguments: dict[str, Any],
    valid: tuple[str, ...],
    tells: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> dict[str, Any]:
    """`action` from a synonym, or from arguments that admit one answer."""

    out = dict(arguments)

    action = _first_text(out, _ACTION_KEYS)

    if action:
        tidy = action.strip().lower().replace(" ", "_").replace("-", "_")
        out["action"] = tidy
        return out

    for candidate, required in tells:
        if candidate not in valid:
            continue

        if all(out.get(field) not in (None, "", []) for field in required):
            out["action"] = candidate
            return out

    # Exactly one action and nothing to distinguish it: there is only
    # one thing the call could have meant.
    if len(valid) == 1:
        out["action"] = valid[0]

    return out
