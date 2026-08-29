import tempfile
from pathlib import Path

import pytest

from src.brain.deliberation import Deliberator
from src.brain.types import Notable, Proposal, ProposalKind
from src.memory.store import MemoryStore


class Reasoner:
    def __init__(self, proposals=None):
        self._proposals = proposals or []

    def decide(self, ctx):
        return list(self._proposals)


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as tmp:
        s = MemoryStore(Path(tmp) / "m.sqlite3")
        yield s
        s.close()


class _Learner:
    def recall_context(self):
        return ""


def _delib(store, reasoner):
    return Deliberator(reasoner, store, _Learner(), [], "full")


def test_reasoner_proposals_pass_through(store):
    d = _delib(store, Reasoner([Proposal(ProposalKind.SPEAK, "hi there")]))
    out = d.deliberate([Notable("x", "x", "something", "info")], None)
    assert [p.message for p in out] == ["hi there"]


def test_silent_reasoner_still_voices_warn_and_critical(store):
    d = _delib(store, Reasoner([]))

    warn = d.deliberate([Notable("res", "disk.C:.free_gb", "Disk C: 6 GB free", "warn")], None)
    assert len(warn) == 1 and warn[0].kind is ProposalKind.SPEAK
    assert warn[0].urgency == "normal"

    crit = d.deliberate([Notable("net", "firewall.Public.enabled", "Firewall OFF", "critical")], None)
    assert len(crit) == 1 and crit[0].urgency == "high"


def test_silent_reasoner_stays_silent_for_info(store):
    d = _delib(store, Reasoner([]))
    assert d.deliberate([Notable("x", "x", "minor info", "info")], None) == []


def test_safety_net_skips_topic_the_reasoner_covered(store):
    d = _delib(store, Reasoner([
        Proposal(ProposalKind.SPEAK, "Your disk is almost full, want me to clean temp files?")
    ]))
    out = d.deliberate(
        [Notable("res", "disk.C:.free_gb", "Disk C: 5 GB free", "warn")], None
    )
    assert len(out) == 1  # not duplicated


def test_safety_net_respects_suppression(store):
    store.add_fact("SUPPRESS: disk space", category="correction")
    d = _delib(store, Reasoner([]))
    out = d.deliberate(
        [Notable("res", "disk.C:.free_gb", "Disk space low: 5 GB free", "warn")], None
    )
    assert out == []
