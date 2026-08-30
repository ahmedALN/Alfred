import asyncio

from src.brain.audit import AuditLog
from src.brain.orchestrator import BrainLoop
from src.brain.policy import Policy
from src.brain.types import Notable, Proposal, ProposalKind


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakePerception:
    """Fires a notable on the first sense() only, like real hysteresis."""

    def __init__(self):
        self._fired = False

    def sense(self):
        if self._fired:
            return [], []
        self._fired = True
        return [Notable("res", "disk.C:.free_gb", "Disk C: 5 GB free", "warn")], []


class FakeDeliberator:
    def __init__(self, proposals):
        self._p = proposals

    def deliberate(self, notables, session_id):
        return list(self._p) if notables else []


def _loop(tmp_path, clock, proposals, grace=60.0):
    audit = AuditLog(tmp_path / "a.sqlite3")
    spoken = []

    async def speak(t):
        spoken.append(t)

    loop = BrainLoop(
        perception=FakePerception(),
        deliberator=FakeDeliberator(proposals),
        policy=Policy("full", {"system_info"}, surface="brain"),
        registry=None,
        audit=audit,
        learner=None,
        speak=speak,
        get_session_id=lambda: "s",
        min_speak_gap_seconds=0.0,
        startup_grace_seconds=grace,
        speak_proactive=True,
        monotonic=clock,
        fullscreen_probe=lambda: False,
    )
    return loop, spoken, audit


def test_proactive_held_during_grace_then_released(tmp_path):
    clock = Clock()
    loop, spoken, audit = _loop(
        tmp_path, clock,
        [Proposal(ProposalKind.SPEAK, "your disk is low")],
        grace=60.0,
    )

    # tick 1: inside the grace window -> nothing said
    asyncio.run(loop.run_once())
    assert spoken == []

    # 90s later, still low disk -> the held line comes out
    clock.t += 90
    asyncio.run(loop.run_once())
    assert spoken == ["(System: proactive) your disk is low"]
    audit.close()


def test_urgent_lines_are_not_held(tmp_path):
    clock = Clock()
    loop, spoken, audit = _loop(
        tmp_path, clock,
        [Proposal(ProposalKind.SPEAK, "battery at 3%", urgency="high")],
    )
    asyncio.run(loop.run_once())
    assert spoken == ["(System: proactive) battery at 3%"]
    audit.close()
