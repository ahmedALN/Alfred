from __future__ import annotations

import re
import uuid
from typing import Any

from src.brain.policy import Policy
from src.brain.skill_store import SkillStore
from src.brain.types import Proposal, ProposalKind, Verdict

try:  # embeddings are optional
    from src.memory.embeddings import cosine_similarity
except Exception:  # noqa: BLE001
    cosine_similarity = None  # type: ignore


_STOPWORDS = {
    "a", "an", "the", "to", "for", "of", "on", "in", "at", "my", "me",
    "please", "can", "you", "could", "would", "and", "then", "with", "up",
    "it", "that", "this", "i", "want", "need", "get", "go", "some",
}

_MATCH_THRESHOLD = 0.62
_MIN_CONFIDENCE = 0.5
_MAX_STEPS = 8

# Arg keys that carry free-form user content (as opposed to structural
# things like action names, control names, hotkeys). Only these become
# parameter slots when distilling a skill.
# Argument values that came from the request rather than from the app,
# so they change when the request changes.
#
# "name" is deliberately NOT here, though it was tried. A control's
# label often coincides with a word in the request - "play drake on
# spotify" contains "play", and so does the Play button - and slotting
# it substitutes the new request's subject into a button name, which
# breaks the skill outright. The cost of leaving it out is smaller: a
# read-back step keeps the literal it was taught with, which reads the
# wrong thing but does not press the wrong thing.
_FREEFORM_KEYS = {
    "text", "query", "q", "search", "value", "content", "message",
    "prompt", "term", "phrase", "input",
}


def _norm(word: str) -> str:
    return word.lower().strip(".,!?;:'\"()")


def _tokens(text: str) -> list[str]:
    return [_norm(w) for w in text.split() if _norm(w)]


def _keywords(text: str) -> list[str]:
    return [t for t in _tokens(text) if t not in _STOPWORDS and len(t) > 1]


def _slug(text: str, words: int = 4) -> str:
    kw = _keywords(text)[:words]
    return "-".join(kw) or "skill"


# --------------------------------------------------------------------
# template alignment: fill {slot}s in a template from a fresh request
# --------------------------------------------------------------------

# A slot, with whatever punctuation the sentence happened to put round
# it. Anchoring to the whole token meant "{p1}." - a parameter at the end
# of a sentence, which is where parameters usually are - was not
# recognised as a slot at all. It was matched as the literal text
# "{p1}.", never found, and the value was never filled, so every skill
# whose last word is its parameter failed on the spot with no steps run.
_SLOT_RE = re.compile(r"^[^\w{]*\{(\w+)\}[^\w}]*$")


def align(template: str, request: str) -> dict[str, str] | None:
    """Extract slot values by aligning ``template`` (with ``{slot}`` tokens)
    against ``request``. Returns ``None`` if a slot can't be filled."""

    t = template.split()
    r = request.split()
    out: dict[str, str] = {}
    ri = 0

    for ti, tok in enumerate(t):
        m = _SLOT_RE.match(tok)
        if not m:
            want = _norm(tok)
            j = ri
            while j < len(r) and _norm(r[j]) != want and j - ri < 4:
                j += 1
            if j < len(r) and _norm(r[j]) == want:
                ri = j + 1
            continue

        slot = m.group(1)
        nxt: str | None = None
        for k in range(ti + 1, len(t)):
            if not _SLOT_RE.match(t[k]):
                nxt = _norm(t[k])
                break

        if nxt is None:
            grabbed = r[ri:]
            ri = len(r)
        else:
            grabbed = []
            while ri < len(r) and _norm(r[ri]) != nxt:
                grabbed.append(r[ri])
                ri += 1
            if ri >= len(r):
                return None  # never found the anchor word after the slot

        if not grabbed:
            return None
        # The value carries the sentence's punctuation - "Celeste." - and
        # what is wanted is the name.
        out[slot] = " ".join(grabbed).strip().strip(".,!?;:")

    return out


def apply_params(steps: list[dict[str, Any]], values: dict[str, str]) -> list[dict[str, Any]]:
    """Return a copy of ``steps`` with every ``{slot}`` in a string arg
    replaced by its value."""

    def sub(obj: Any) -> Any:
        if isinstance(obj, str):
            for slot, val in values.items():
                obj = obj.replace("{" + slot + "}", val)
            return obj
        if isinstance(obj, dict):
            return {k: sub(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [sub(v) for v in obj]
        return obj

    return [
        {"tool": s["tool"], "args": sub(s.get("args", {}))}
        for s in steps
    ]


# --------------------------------------------------------------------


class SkillLibrary:
    """
    Matches requests to learned skills, distills verified task successes
    into new skills, and tracks each skill's confidence over time.

    Execution lives in TaskAgent.replay - this class is pure data +
    matching so it's cheap to test.
    """

    def __init__(
        self,
        store: SkillStore,
        *,
        policy: Policy | None = None,
        embedder: Any = None,
        enabled: bool = True,
    ) -> None:
        self._store = store
        self._policy = policy
        self._embedder = embedder
        self.enabled = enabled

    # ---- matching --------------------------------------------------

    def match(self, request: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None

        req_kw = set(_keywords(request))
        if not req_kw:
            return None

        req_vec = self._embed(request)
        best: dict[str, Any] | None = None
        best_score = 0.0

        for skill in self._store.all():
            if skill["success"] < 1 or skill["confidence"] < _MIN_CONFIDENCE:
                continue

            kw = set(skill["keywords"])
            if not kw:
                continue

            overlap = len(req_kw & kw) / len(kw)
            score = overlap

            if req_vec is not None and skill["template"]:
                tv = self._embed(skill["template"])
                if tv is not None:
                    score = 0.4 * overlap + 0.6 * _cos(req_vec, tv)

            if score < _MATCH_THRESHOLD:
                continue

            if skill["params"] and align(skill["template"], request) is None:
                continue

            if score > best_score:
                best, best_score = skill, score

        return best

    # ---- distillation --------------------------------------------

    def distill(
        self,
        goal: str,
        trace: list[tuple[str, dict[str, Any]]],
        *,
        verify: str = "",
        app: str = "",
    ) -> dict[str, Any] | None:
        """Turn a verified tool sequence into an (unsaved) skill dict."""

        trace = [(t, a) for t, a in trace if t]
        if not trace or len(trace) > _MAX_STEPS:
            return None

        goal_l = goal.lower()
        slots: dict[str, str] = {}        # literal text -> slot name
        template = goal

        def _slotify(value: str) -> str:
            v = value.strip()
            if not v or len(v) < 2 or v.lower() not in goal_l:
                return value
            if v.lower() not in slots:
                slots[v.lower()] = f"p{len(slots)}"
            return "{" + slots[v.lower()] + "}"

        def _slotify_args(obj: Any, freeform: bool) -> Any:
            if isinstance(obj, str):
                return _slotify(obj) if freeform else obj
            if isinstance(obj, dict):
                return {
                    k: _slotify_args(val, freeform or k.lower() in _FREEFORM_KEYS)
                    for k, val in obj.items()
                }
            if isinstance(obj, list):
                return [_slotify_args(v, freeform) for v in obj]
            return obj

        steps: list[dict[str, Any]] = []
        for tool, args in trace:
            steps.append({
                "tool": tool,
                "args": _slotify_args(args, False),
            })

        for literal, slot in slots.items():
            template = re.sub(
                re.escape(literal), "{" + slot + "}", template,
                count=1, flags=re.IGNORECASE,
            )

        for existing in self._store.all(include_disabled=True):
            if existing["template"].strip().lower() == template.strip().lower():
                return None  # already know this one

        params = list(slots.values())
        tier, note = self._classify(trace)
        kw = [t for t in _keywords(goal) if t not in slots]

        return {
            "id": uuid.uuid4().hex[:10],
            "name": _slug(goal),
            "template": template,
            "keywords": kw,
            "params": params,
            "steps": steps,
            "verify": verify or goal,
            "app": app,
            "tier": tier,
            "danger_note": note,
            "success": 1,
            "fail": 0,
            "confidence": 0.55,
            "unconfirmed": tier == "dangerous",
        }

    def save(self, skill: dict[str, Any]) -> None:
        self._store.upsert(skill)

    # ---- feedback ------------------------------------------------

    def reward(self, skill_id: str) -> None:
        s = self._store.get(skill_id)
        if not s:
            return
        conf = min(0.99, s["confidence"] + 0.12)
        unconf = s["unconfirmed"] and (s["success"] + 1) < 3
        s["unconfirmed"] = unconf
        self._store.upsert(s)
        self._store.record_use(skill_id, ok=True, confidence=conf)

    def penalize(self, skill_id: str) -> None:
        s = self._store.get(skill_id)
        if not s:
            return
        conf = s["confidence"] * 0.7
        self._store.record_use(skill_id, ok=False, confidence=conf)
        if conf < 0.25:
            self._store.set_disabled(skill_id, True)

    def needs_confirmation(self, skill: dict[str, Any]) -> bool:
        return skill.get("tier") == "dangerous" or bool(skill.get("unconfirmed"))

    # ---- internals ----------------------------------------------

    def _classify(
        self, trace: list[tuple[str, dict[str, Any]]]
    ) -> tuple[str, str]:
        """A skill is 'dangerous' if any step would still need the user's
        OK even when they asked for it directly (voice surface): mutating
        PowerShell, service changes, bulk deletes, and the like."""
        if self._policy is None:
            return "ordinary", ""
        for tool, args in trace:
            proposal = Proposal(
                kind=ProposalKind.ACT, message=f"use {tool}",
                tool=tool, args=args,
            )
            try:
                decision = self._policy.evaluate(proposal)
            except Exception:  # noqa: BLE001
                continue
            if decision.verdict is not Verdict.AUTO:
                return "dangerous", f"{tool} ({decision.reason})"
        return "ordinary", ""

    def _embed(self, text: str) -> list[float] | None:
        if self._embedder is None:
            return None
        try:
            return self._embedder.embed(text)
        except Exception:  # noqa: BLE001
            return None


def _cos(a: list[float], b: list[float]) -> float:
    if cosine_similarity is None:
        return 0.0
    try:
        return float(cosine_similarity(a, b))
    except Exception:  # noqa: BLE001
        return 0.0
