"""Knowledge is only worth having if it comes back when it is needed."""

from src.knowledge import WINDOWS_PLAYBOOK


class _Fact:
    def __init__(self, content, source="playbook"):
        self.content = content
        self.source = source


class _Learner:
    """Recall with a confidence bar, like the real one."""

    def __init__(self, strong=(), weak=()):
        self._strong = list(strong)
        self._weak = list(weak)
        self.thresholds = []

    def recall(self, query, top_k=5, threshold=None):
        self.thresholds.append(threshold)
        pool = self._strong if threshold is None else self._weak
        return [_Fact(c) for c in pool[:top_k]]


def _agent(learner):
    from src.brain.agent import TaskAgent

    agent = object.__new__(TaskAgent)
    agent._learner = learner
    return agent


def test_a_near_miss_is_better_than_silence():
    """"What have you been doing today" scored 0.535 against a 0.55 bar
    and came back with nothing. The planner can ignore a weak hint; it
    cannot use one it never saw."""
    learner = _Learner(strong=[], weak=["episodes records what Alfred did"])

    text = _agent(learner)._relevant_knowledge("what have you been doing")

    assert "episodes" in text
    assert learner.thresholds == [None, 0.42]


def test_a_confident_match_is_not_diluted_by_weaker_ones():
    learner = _Learner(strong=["the good one"], weak=["a vague one"])

    text = _agent(learner)._relevant_knowledge("open steam")

    assert text == "- the good one"
    assert learner.thresholds == [None]


def test_planning_survives_a_broken_memory():
    class Broken:
        def recall(self, *a, **k):
            raise RuntimeError("memory is down")

    assert _agent(Broken())._relevant_knowledge("anything") == ""


def test_the_playbook_has_no_duplicates():
    """Two entries saying the same thing crowd out a third."""
    seen = [c.strip().lower() for _, c in WINDOWS_PLAYBOOK]
    assert len(seen) == len(set(seen))


def test_every_playbook_entry_is_usable():
    for category, content in WINDOWS_PLAYBOOK:
        assert category in ("system", "correction", "preference", "fact")
        assert 40 < len(content) < 700, content[:60]
