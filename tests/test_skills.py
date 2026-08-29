import json

from src.brain.agent import TaskAgent
from src.brain.policy import Policy
from src.brain.skill_store import SkillStore
from src.brain.skills import SkillLibrary, align, apply_params
from tests._taskfakes import KNOWN, DispatchChat, FakeRegistry


# --------------------------------------------------------------------
# template alignment
# --------------------------------------------------------------------

def test_align_single_slot():
    assert align("play {p0} on spotify", "play adele on spotify") == {
        "p0": "adele"
    }


def test_align_multiword_slot():
    assert align("play {p0} on spotify", "play the weeknd on spotify") == {
        "p0": "the weeknd"
    }


def test_align_trailing_slot():
    assert align("search for {p0}", "search for drake songs") == {
        "p0": "drake songs"
    }


def test_align_unfillable_returns_none():
    assert align("play {p0} on spotify", "pause the music") is None


def test_apply_params_substitutes_nested_args():
    steps = [{"tool": "ui_control", "args": {"action": "type", "text": "{p0}"}}]
    out = apply_params(steps, {"p0": "drake"})
    assert out[0]["args"]["text"] == "drake"


# --------------------------------------------------------------------
# distillation
# --------------------------------------------------------------------

def _lib(tmp_path, **kw):
    store = SkillStore(tmp_path / "skills.sqlite3")
    pol = Policy("full", KNOWN, surface="voice")
    return SkillLibrary(store, policy=pol, **kw), store


def test_distill_makes_param_slot_from_request_literal(tmp_path):
    lib, store = _lib(tmp_path)
    trace = [
        ("open_app", {"name": "spotify"}),
        ("ui_control", {"action": "type", "text": "drake"}),
        ("ui_control", {"action": "click", "name": "Play"}),
    ]
    skill = lib.distill("play drake on spotify", trace, verify="a track is playing")

    assert skill is not None
    assert skill["params"] == ["p0"]
    assert skill["template"] == "play {p0} on spotify"
    typed = [s for s in skill["steps"] if s["args"].get("action") == "type"][0]
    assert typed["args"]["text"] == "{p0}"
    assert skill["tier"] == "ordinary"


def test_distill_flags_dangerous_routine(tmp_path):
    lib, store = _lib(tmp_path)
    trace = [("powershell", {"command": "Move-Item C:\\a C:\\b -Recurse"})]
    skill = lib.distill("archive my old files", trace)

    assert skill["tier"] == "dangerous"
    assert skill["unconfirmed"] is True
    assert "powershell" in skill["danger_note"]
    assert lib.needs_confirmation(skill)


def test_distill_rejects_duplicate_template(tmp_path):
    lib, store = _lib(tmp_path)
    trace = [("open_app", {"name": "spotify"})]
    first = lib.distill("open spotify", trace)
    lib.save(first)
    assert lib.distill("open spotify", trace) is None


# --------------------------------------------------------------------
# matching
# --------------------------------------------------------------------

def test_match_finds_saved_skill_by_keywords(tmp_path):
    lib, store = _lib(tmp_path)
    skill = lib.distill("play drake on spotify",
                        [("ui_control", {"action": "type", "text": "drake"})],
                        verify="a track is playing")
    lib.save(skill)

    hit = lib.match("play adele on spotify")
    assert hit is not None and hit["id"] == skill["id"]


def test_match_misses_unrelated_request(tmp_path):
    lib, store = _lib(tmp_path)
    lib.save(lib.distill("play drake on spotify",
                         [("ui_control", {"action": "type", "text": "drake"})]))
    assert lib.match("what's my cpu temperature") is None


def test_match_skips_disabled_skill(tmp_path):
    lib, store = _lib(tmp_path)
    skill = lib.distill("play drake on spotify",
                        [("ui_control", {"action": "type", "text": "drake"})])
    lib.save(skill)
    store.set_disabled(skill["id"], True)
    assert lib.match("play adele on spotify") is None


def test_penalize_disables_skill_when_confidence_craters(tmp_path):
    lib, store = _lib(tmp_path)
    skill = lib.distill("play drake on spotify",
                        [("ui_control", {"action": "type", "text": "drake"})])
    lib.save(skill)
    for _ in range(5):
        lib.penalize(skill["id"])
    assert store.get(skill["id"])["disabled"] is True


# --------------------------------------------------------------------
# replay through the agent
# --------------------------------------------------------------------

def _agent(chat, reg):
    return TaskAgent(
        chat, reg, Policy("full", KNOWN, surface="brain"),
        policy_voice=Policy("full", KNOWN, surface="voice"),
    )


def test_agent_replays_skill_with_filled_params(tmp_path):
    lib, store = _lib(tmp_path)
    skill = lib.distill(
        "play drake on spotify",
        [("ui_control", {"action": "type", "text": "drake"}),
         ("ui_control", {"action": "click", "name": "Play"})],
        verify="a track is playing",
    )
    lib.save(skill)

    reg = FakeRegistry()
    chat = DispatchChat(verify=True)
    result = _agent(chat, reg).replay(skill, "play adele on spotify",
                                      source="voice")

    assert result.status == "done"
    assert reg.executed == [
        ("ui_control", {"action": "type", "text": "adele"}),
        ("ui_control", {"action": "click", "name": "Play"}),
    ]
    assert chat.plan_calls == 0  # no planning on replay


def test_agent_replay_does_not_claim_done_when_nothing_ran(tmp_path):
    lib, store = _lib(tmp_path)
    skill = lib.distill("play drake on spotify",
                        [("ui_control", {"action": "type", "text": "drake"})],
                        verify="a track is playing")
    lib.save(skill)

    reg = FakeRegistry(results={"ui_control": {"status": "error", "error": "x"}})
    result = _agent(DispatchChat(verify=True), reg).replay(
        skill, "play adele on spotify", source="voice"
    )
    assert result.status == "failed"
    assert result.verified == []


def test_replay_reports_missing_param(tmp_path):
    lib, store = _lib(tmp_path)
    skill = lib.distill("play {p0} on spotify".replace("{p0}", "drake"),
                        [("ui_control", {"action": "type", "text": "drake"})])
    lib.save(skill)

    result = _agent(DispatchChat(), FakeRegistry()).replay(
        skill, "do something unrelated entirely", source="voice"
    )
    assert result.status == "failed"
