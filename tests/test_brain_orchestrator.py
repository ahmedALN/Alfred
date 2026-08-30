import asyncio
from pathlib import Path

import pytest

from src.brain.audit import AuditLog
from src.brain.orchestrator import BrainLoop
from src.brain.policy import Policy
from src.brain.types import Notable, Proposal, ProposalKind


# ---------------------------------------------------------------- fakes


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakePerception:
    def __init__(self, notables: list[Notable]) -> None:
        self._notables = notables

    def sense(self):
        obs = []
        return list(self._notables), obs


class FakeDeliberator:
    def __init__(self, proposals: list[Proposal]) -> None:
        self.proposals = proposals
        self.calls = 0

    def deliberate(self, notables, session_id):
        self.calls += 1
        return list(self.proposals)


class FakeRegistry:
    def __init__(self, result=None) -> None:
        self.result = result or {"status": "success"}
        self.executed: list[tuple[str, dict]] = []

    def execute(self, name, args):
        self.executed.append((name, args))
        return self.result


class FakeLearner:
    def __init__(self) -> None:
        self.remembered: list[dict] = []

    def remember(self, content, category="general", source="conversation"):
        self.remembered.append(
            {"content": content, "category": category, "source": source}
        )
        return {"status": "stored"}


KNOWN_TOOLS = {"powershell", "system_info", "open_app", "remember", "recall"}


def _notable():
    return Notable("resources", "disk.C:.free_gb", "Disk C: 5 GB free", "warn")


def build_loop(
    tmp_path: Path,
    proposals: list[Proposal],
    *,
    clock: Clock | None = None,
    registry: FakeRegistry | None = None,
    learner: FakeLearner | None = None,
    min_speak_gap: float = 600.0,
    autonomy: str = "full",
):
    clock = clock or Clock()
    audit = AuditLog(tmp_path / "audit.sqlite3")
    spoken: list[str] = []

    async def speak(text: str) -> None:
        spoken.append(text)

    loop = BrainLoop(
        perception=FakePerception([_notable()]),
        deliberator=FakeDeliberator(proposals),
        policy=Policy(autonomy, KNOWN_TOOLS),
        registry=registry or FakeRegistry(),
        audit=audit,
        learner=learner or FakeLearner(),
        speak=speak,
        get_session_id=lambda: "sess-1",
        tick_seconds=90.0,
        min_speak_gap_seconds=min_speak_gap,
        startup_grace_seconds=0.0,
        speak_proactive=True,
        monotonic=clock,
        fullscreen_probe=lambda: False,
    )
    return loop, spoken, audit, clock


def _kinds(audit: AuditLog) -> list[str]:
    return [row["kind"] for row in audit.recent(limit=100)]


# ---------------------------------------------------------------- tests


def test_speak_proposal_is_spoken_and_audited(tmp_path):
    loop, spoken, audit, _ = build_loop(
        tmp_path, [Proposal(ProposalKind.SPEAK, "Your disk is nearly full.")]
    )

    asyncio.run(loop.run_once())

    assert spoken == ["(System: proactive) Your disk is nearly full."]
    kinds = _kinds(audit)
    assert "tick" in kinds and "notable" in kinds
    assert "decision" in kinds and "spoken" in kinds
    audit.close()


def test_forbidden_proposal_is_blocked_not_executed(tmp_path):
    registry = FakeRegistry()
    loop, spoken, audit, _ = build_loop(
        tmp_path,
        [
            Proposal(
                ProposalKind.ACT,
                "clean up windows",
                tool="powershell",
                args={"command": "Remove-Item -Recurse C:\\Windows"},
            )
        ],
        registry=registry,
    )

    asyncio.run(loop.run_once())

    assert registry.executed == []
    assert spoken == []
    assert "blocked" in _kinds(audit)
    audit.close()


def test_auto_readonly_action_executes_and_reports(tmp_path):
    registry = FakeRegistry(result={"status": "success", "data": {}})
    loop, spoken, audit, _ = build_loop(
        tmp_path,
        [
            Proposal(
                ProposalKind.ACT,
                "checked your disk usage",
                tool="system_info",
                args={"query": "disks"},
            )
        ],
        registry=registry,
    )

    asyncio.run(loop.run_once())

    assert registry.executed == [("system_info", {"query": "disks"})]
    assert spoken == ["(System: proactive) checked your disk usage"]
    kinds = _kinds(audit)
    assert "action" in kinds and "action_result" in kinds
    audit.close()


def test_multiple_speaks_combine_into_one_message(tmp_path):
    loop, spoken, audit, _ = build_loop(
        tmp_path,
        [
            Proposal(ProposalKind.SPEAK, "disk is low"),
            Proposal(ProposalKind.SPEAK, "a reboot is pending"),
        ],
    )

    asyncio.run(loop.run_once())

    assert spoken == [
        "(System: proactive) A few things - disk is low; a reboot is pending"
    ]
    audit.close()


def test_urgent_and_minor_in_one_tick_are_said_together_urgent_first(tmp_path):
    loop, spoken, audit, _ = build_loop(
        tmp_path,
        [
            Proposal(ProposalKind.SPEAK, "minor note"),
            Proposal(ProposalKind.SPEAK, "battery critical", urgency="high"),
        ],
    )

    asyncio.run(loop.run_once())

    assert spoken == [
        "(System: proactive) A few things - battery critical; minor note"
    ]
    audit.close()
    audit.close()


def test_pause_phrase_halts_ticks(tmp_path):
    loop, spoken, audit, _ = build_loop(
        tmp_path, [Proposal(ProposalKind.SPEAK, "something")]
    )

    asyncio.run(loop.note_user_reply("Alfred, stop"))
    asyncio.run(loop.run_once())

    assert spoken == []
    audit.close()


def test_dnd_phrase_suppresses_speech(tmp_path):
    loop, spoken, audit, _ = build_loop(
        tmp_path, [Proposal(ProposalKind.SPEAK, "something")]
    )

    asyncio.run(loop.note_user_reply("do not disturb me"))
    asyncio.run(loop.run_once())

    assert spoken == []
    assert "spoken" in _kinds(audit)  # recorded as suppressed
    audit.close()


def test_suppression_phrase_persists_fact(tmp_path):
    learner = FakeLearner()
    loop, _, audit, _ = build_loop(
        tmp_path, [Proposal(ProposalKind.SPEAK, "x")], learner=learner
    )

    asyncio.run(loop.note_user_reply("stop telling me about disk space"))

    assert len(learner.remembered) == 1
    assert learner.remembered[0]["content"] == "SUPPRESS: disk space"
    audit.close()


def test_stated_goal_is_remembered(tmp_path):
    learner = FakeLearner()
    loop, _, audit, _ = build_loop(
        tmp_path, [Proposal(ProposalKind.SPEAK, "x")], learner=learner
    )

    asyncio.run(loop.note_user_reply(
        "I'm trying to set up a python dev environment on this machine"
    ))

    assert learner.remembered
    assert learner.remembered[0]["content"].startswith("GOAL: set up a python")
    audit.close()


def test_ordinary_reply_sets_no_goal(tmp_path):
    learner = FakeLearner()
    loop, _, audit, _ = build_loop(
        tmp_path, [Proposal(ProposalKind.SPEAK, "x")], learner=learner
    )
    asyncio.run(loop.note_user_reply("what time is it"))
    assert learner.remembered == []
    audit.close()


def test_confirm_then_yes_executes_action(tmp_path):
    registry = FakeRegistry()
    loop, spoken, audit, _ = build_loop(
        tmp_path,
        [
            Proposal(
                ProposalKind.ACT,
                "create a cleanup script",
                tool="powershell",
                args={"command": "New-Item C:\\temp\\x.ps1 -ItemType File"},
            )
        ],
        registry=registry,
    )

    asyncio.run(loop.run_once())
    assert registry.executed == []  # waiting on confirmation
    assert spoken and spoken[0].endswith("go ahead?")

    asyncio.run(loop.note_user_reply("yes, go ahead"))
    assert registry.executed == [
        ("powershell", {"command": "New-Item C:\\temp\\x.ps1 -ItemType File"})
    ]
    audit.close()


# ------------------------------------------- proactive speech is opt-in


def test_the_brain_does_not_talk_to_itself_out_loud(tmp_path):
    """Most of what the brain comes up with on a tick is a note to
    itself - "I'm remembering that ui_control has a tree action". Spoken,
    that is Alfred muttering his own reference material at the user."""
    loop, spoken, audit, _ = build_loop(
        tmp_path,
        [Proposal(kind=ProposalKind.SPEAK, message="I'm noting that ...")],
    )
    loop._speak_proactive = False

    asyncio.run(loop.run_once())

    assert spoken == []


def test_what_it_would_have_said_is_still_kept(tmp_path):
    """Silenced, not discarded - the observation stays auditable."""
    loop, spoken, audit, _ = build_loop(
        tmp_path,
        [Proposal(kind=ProposalKind.SPEAK, message="disk is nearly full")],
    )
    loop._speak_proactive = False

    asyncio.run(loop.run_once())

    held = [
        row for row in audit.recent(limit=100)
        if row["kind"] == "spoken"
        and "nearly full" in str(row["payload"])
    ]
    assert held, "the suppressed line should still be recorded"
    assert held[0]["payload"]["suppressed"] is True


def test_it_still_acts_while_silent(tmp_path):
    """Silence is about chatter, not about doing nothing."""
    registry = FakeRegistry()
    loop, spoken, _, _ = build_loop(
        tmp_path,
        [Proposal(ProposalKind.ACT, "checked your disk usage",
                  tool="system_info", args={"query": "disks"})],
        registry=registry,
    )
    loop._speak_proactive = False

    asyncio.run(loop.run_once())

    assert registry.executed and spoken == []
