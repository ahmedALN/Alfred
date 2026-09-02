"""Working out an app nobody has taught Alfred yet.

Some apps draw their own buttons and give them no names - Qt apps, game
launchers, anything with a custom skin. The accessibility layer can say
exactly WHERE those controls are and nothing about what they do; a
screenshot says what they are called and only roughly where. Neither is
enough alone. Put them together and the app becomes usable by name.

Clicking unknown buttons to find out what they do is the other way, and
a worse one: in MultiMC one of them is Delete.
"""

from __future__ import annotations

import re
from typing import Any

_PROMPT = (
    "This is a screenshot of one application window, {w} pixels wide and "
    "{h} tall.\n"
    "List every clickable control that has a VISIBLE TEXT LABEL - "
    "buttons, tabs, menu entries, list rows, links.\n"
    "One per line, exactly:\n"
    "  <label> | <x>, <y>\n"
    "where <label> is the text as written on screen and x, y are the "
    "centre of that control in pixels FROM THE TOP-LEFT OF THIS IMAGE.\n"
    "Do not include the window's title bar buttons, and do not invent "
    "controls you cannot read. If you can see no labelled controls, "
    "reply with the single word NONE."
)

# "Launch | 626, 214"  /  "Launch - 626, 214"  /  "Launch (626, 214)"
# Buttons that destroy something. Reading labels off a screen is
# accurate about WHAT is there and approximate about WHERE, and a
# one-row error on this list means deleting the thing next to the thing
# you meant. These are never learned from a picture - the user names
# them, or Alfred does without them.
DESTRUCTIVE = re.compile(
    r"\b(delete|remove|uninstall|erase|wipe|format|reset|clear|purge|"
    r"destroy|revoke|unpair|forget|drop|discard)\b",
    re.I,
)

# Controls within this many pixels of each other horizontally are
# treated as one vertical stack.
_COLUMN_WIDTH = 70

_LINE = re.compile(
    r"^\s*(?:[-*\d.)\s]*)(?P<label>.+?)\s*[|\-–(]\s*"
    r"(?P<x>\d{1,5})\s*[, ]\s*(?P<y>\d{1,5})\s*\)?\s*$"
)


def read_labels(analysis: str) -> list[dict[str, Any]]:
    """Turn the vision model's reply into labels with positions."""
    found: list[dict[str, Any]] = []

    for line in (analysis or "").splitlines():
        line = line.strip()
        if not line or line.upper() == "NONE":
            continue

        match = _LINE.match(line)
        if not match:
            continue

        label = " ".join(match.group("label").split()).strip(" :-|")
        if not label or len(label) > 60 or label.startswith("<"):
            continue

        found.append({
            "label": label,
            "x": int(match.group("x")),
            "y": int(match.group("y")),
        })

    return found


def pair(labels: list[dict[str, Any]], controls: list[dict[str, Any]],
         rect: list[int], named: list[dict[str, Any]] | None = None,
         tolerance: int | None = None) -> list[dict[str, Any]]:
    """Match each read label to the control it names.

    The model reads labels accurately and places them approximately, and
    its error is systematic - a scale that is not quite right, an offset
    from a title bar it did or did not count. Fitting that by counting
    how many labels land near SOME control does not work: a column of
    evenly spaced buttons lines up just as well one row out, and picking
    between equal answers put every MultiMC label two rows off.

    So the transform is anchored instead. Most windows contain controls
    the accessibility layer CAN name, the model reads those too, and a
    label whose text matches a known control is a fixed point tying the
    two coordinate systems together. Fit on those, then apply it to the
    nameless ones.
    """
    height = max(1, rect[3] - rect[1])
    if tolerance is None:
        tolerance = max(10, min(28, height // 60))

    transform = _anchor(labels, named or [], rect)
    if transform is None:
        # Nothing to anchor on. Better to learn nothing than to learn
        # fifteen confident wrong positions.
        return []

    return _assign(labels, controls, rect, tolerance, *transform)


def _anchor(labels: list[dict[str, Any]], named: list[dict[str, Any]],
            rect: list[int]) -> tuple[float, float, int, int] | None:
    """Fit scale and offset from labels whose text names a known control."""
    if len(named) < 2:
        return None

    by_text: dict[str, tuple[int, int]] = {}
    for control in named:
        text = " ".join((control.get("name") or "").split()).lower()
        if text and text not in by_text:
            by_text[text] = tuple(control["center"])

    pairs: list[tuple[dict[str, Any], tuple[int, int]]] = []
    for entry in labels:
        text = entry["label"].strip().lower()
        hit = by_text.get(text)
        if hit is None:
            for known, centre in by_text.items():
                if known.startswith(text) and len(text) >= 4:
                    hit = centre
                    break
        if hit is not None:
            pairs.append((entry, hit))

    if len(pairs) < 2:
        return None

    return _least_squares(pairs)


def _least_squares(pairs) -> tuple[float, float, int, int] | None:
    """The straight line through the anchors, per axis."""

    def fit(reported: list[int], actual: list[int]) -> tuple[float, int]:
        n = len(reported)
        spread = max(reported) - min(reported)
        if n < 2 or spread < 5:
            # All at the same place: no scale to infer, only a shift.
            return 1.0, int(sum(actual) / n - sum(reported) / n)

        mean_r = sum(reported) / n
        mean_a = sum(actual) / n
        top = sum((r - mean_r) * (a - mean_a) for r, a in zip(reported, actual, strict=False))
        bottom = sum((r - mean_r) ** 2 for r in reported) or 1.0
        scale = top / bottom
        return scale, int(mean_a - scale * mean_r)

    scale_x, shift_x = fit([e["x"] for e, _ in pairs], [c[0] for _, c in pairs])
    scale_y, shift_y = fit([e["y"] for e, _ in pairs], [c[1] for _, c in pairs])

    # A wild fit means the anchors disagreed; refuse rather than guess.
    if not (0.2 <= scale_x <= 5.0 and 0.2 <= scale_y <= 5.0):
        return None

    return scale_x, scale_y, shift_x, shift_y


def _assign(labels, controls, rect, tolerance, sx, sy, dx, dy):
    taken: set[int] = set()
    paired: list[dict[str, Any]] = []

    for entry in labels:
        x = int(entry["x"] * sx) + dx
        y = int(entry["y"] * sy) + dy

        best = None
        best_distance = None

        for index, control in enumerate(controls):
            if index in taken:
                continue
            cx, cy = control["center"]
            if abs(cy - y) > tolerance or abs(cx - x) > tolerance * 4:
                continue
            distance = abs(cy - y) * 3 + abs(cx - x)
            if best_distance is None or distance < best_distance:
                best, best_distance = index, distance

        if best is None:
            continue

        taken.add(best)
        control = controls[best]
        paired.append({
            "label": entry["label"],
            "center": control["center"],
            "rel": control["rel"],
            "type": control.get("type", ""),
            "off_by": best_distance,
        })

    return paired


_COLUMN_PROMPT = (
    "This image is a vertical strip cut from an application window, "
    "containing exactly {n} buttons stacked one above another.\n"
    "Read the text on each one, top to bottom.\n"
    "Reply with exactly {n} lines, one label per line, in order, and "
    "nothing else - no numbering, no commentary. If you cannot read {n} "
    "distinct buttons, reply with the single word UNSURE."
)


def columns_of(controls: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group nameless controls into the vertical stacks they form."""
    groups: dict[int, list[dict[str, Any]]] = {}

    for control in controls:
        x = control["center"][0]
        home = next(
            (k for k in groups if abs(k - x) <= _COLUMN_WIDTH), None
        )
        groups.setdefault(home if home is not None else x, []).append(control)

    return [
        sorted(members, key=lambda c: c["center"][1])
        for members in groups.values()
        if len(members) >= 3
    ]


def read_column(png: bytes, rect: list[int], column: list[dict[str, Any]],
                vision: Any, pad: int = 14) -> list[str]:
    """Read one stack of buttons, in order, from a cropped picture.

    Asking where a button is gets an approximate answer; asking what a
    strip of buttons says, in order, gets an exact one. The count is the
    check: if the reply does not have exactly one label per control, the
    reading did not line up and nothing is learned from it.
    """
    try:
        import io

        from PIL import Image
    except Exception:  # noqa: BLE001
        return []

    left, top = rect[0], rect[1]
    xs = [c["center"][0] for c in column]
    ys = [c["center"][1] for c in column]
    widths = [c.get("size", [60, 18])[0] for c in column]
    heights = [c.get("size", [60, 18])[1] for c in column]

    box = (
        max(0, min(xs) - max(widths) // 2 - pad - left),
        max(0, min(ys) - max(heights) // 2 - pad - top),
        max(xs) + max(widths) // 2 + pad - left,
        max(ys) + max(heights) // 2 + pad - top,
    )

    try:
        image = Image.open(io.BytesIO(png)).convert("RGB").crop(box)
        # Small text reads better enlarged.
        image = image.resize((image.width * 2, image.height * 2))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        shot = buffer.getvalue()
    except Exception:  # noqa: BLE001
        return []

    try:
        reply = vision.analyze(shot, _COLUMN_PROMPT.format(n=len(column)))
    except Exception:  # noqa: BLE001
        return []

    lines = [
        " ".join(line.split()).strip(" -|:*0123456789.")
        for line in (reply or "").splitlines()
        if line.strip()
    ]
    lines = [line for line in lines if line and len(line) <= 60]

    lines = [line for line in lines if line.upper() != "UNSURE"]

    # Not every nameless control is a button - a stack of them includes
    # separators and spacers that carry no text. So the reading is not
    # required to have one label per control; it is required to be
    # shorter, and to be long enough to be worth anything.
    if len(lines) < 3 or len(lines) > len(column):
        return []

    return lines


def align(labels: list[str],
          column: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """Lay the labels read from a strip against the controls in it.

    Both are in top-to-bottom order, so the pairing must be too. What is
    unknown is which controls are the ones with no text - the separators
    between groups - so those are skipped, choosing the skips that put
    every label nearest its own place in the stack.
    """
    n_labels, n_controls = len(labels), len(column)
    if not n_labels or n_labels > n_controls:
        return []

    def place(index: int, total: int) -> float:
        return index / (total - 1) if total > 1 else 0.0

    big = float("inf")
    # cost[i][j]: best cost having placed i labels among the first j
    cost = [[big] * (n_controls + 1) for _ in range(n_labels + 1)]
    back = [[0] * (n_controls + 1) for _ in range(n_labels + 1)]
    for j in range(n_controls + 1):
        cost[0][j] = 0.0

    for i in range(1, n_labels + 1):
        for j in range(1, n_controls + 1):
            # Skip this control: it is a separator.
            skip = cost[i][j - 1]
            # Or give it this label.
            take = big
            if cost[i - 1][j - 1] < big:
                take = cost[i - 1][j - 1] + abs(
                    place(i - 1, n_labels) - place(j - 1, n_controls)
                )
            if take <= skip:
                cost[i][j], back[i][j] = take, 1
            else:
                cost[i][j], back[i][j] = skip, 0

    pairs: list[tuple[str, dict[str, Any]]] = []
    i, j = n_labels, n_controls
    while i > 0 and j > 0:
        if back[i][j] == 1:
            pairs.append((labels[i - 1], column[j - 1]))
            i -= 1
        j -= 1

    pairs.reverse()
    return pairs


_ONE_PROMPT = (
    "This is a close crop of a single button or control from an "
    "application. Reply with the text written on it, exactly as it "
    "appears, and nothing else. No punctuation of your own, no "
    "explanation. If it has no readable text, reply with the single "
    "word NONE."
)


def read_one(png: bytes, rect: list[int], control: dict[str, Any],
             vision: Any, pad: int = 6) -> str:
    """What does THIS control say?

    Every attempt to read a whole panel and then work out which label
    belongs to which control drifted - the labels came back right and
    the correspondence came back wrong, which is the worst shape for an
    error to take, because a wrong position is a wrong click. Cropping
    one control removes the question: there is only one thing in the
    picture.
    """
    try:
        import io

        from PIL import Image
    except Exception:  # noqa: BLE001
        return ""

    cx, cy = control["center"]
    width, height = control.get("size", [80, 20])

    box = (
        max(0, cx - width // 2 - pad - rect[0]),
        max(0, cy - height // 2 - pad - rect[1]),
        cx + width // 2 + pad - rect[0],
        cy + height // 2 + pad - rect[1],
    )

    try:
        image = Image.open(io.BytesIO(png)).convert("RGB").crop(box)
        if image.width < 8 or image.height < 6:
            return ""
        # Small text reads far better enlarged.
        image = image.resize((image.width * 4, image.height * 4))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        shot = buffer.getvalue()
    except Exception:  # noqa: BLE001
        return ""

    try:
        reply = vision.analyze(shot, _ONE_PROMPT)
    except Exception:  # noqa: BLE001
        return ""

    text = " ".join((reply or "").split()).strip(" .:-\"'")
    if not text or text.upper() in ("NONE", "UNSURE", "") or len(text) > 40:
        return ""

    return text
