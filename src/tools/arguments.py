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
        out["action"] = action.strip().lower().replace(" ", "_").replace("-", "_")

        if not valid_actions or out["action"] in valid_actions:
            return out

        # A named action nobody recognises is a mistake to report, not
        # one to paper over with a guess.
        return out

    for candidate, required in _UI_ACTION_TELLS:
        if valid_actions and candidate not in valid_actions:
            continue

        if all(
            out.get(field) not in (None, "", [])
            for field in required
        ):
            out["action"] = candidate
            return out

    return out


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
