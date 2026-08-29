from src.brain.perception import Perception, PerceptionThresholds
from src.brain.signals import SignalCollector
from src.brain.types import Observation


class ScriptedCollector(SignalCollector):
    """Emits a pre-scripted list of observations per tick."""

    name = "scripted"

    def __init__(self, script: list[list[Observation]]):
        self._script = script
        self._i = 0

    def collect(self) -> list[Observation]:
        batch = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return batch


def _disk(free_gb: float) -> Observation:
    return Observation(
        source="resources",
        key="disk.C:.free_gb",
        value=free_gb,
        summary=f"Disk C: {free_gb} GB free",
    )


def _fw(enabled: bool) -> Observation:
    return Observation(
        source="network",
        key="firewall.Domain.enabled",
        value=enabled,
        summary=f"Firewall profile Domain: {'on' if enabled else 'OFF'}",
    )


def _ports(ports: list[str]) -> Observation:
    return Observation(
        source="network",
        key="network.listening_ports",
        value=ports,
        summary=f"{len(ports)} listening ports",
    )


def test_low_disk_fires_once_then_stays_quiet():
    collector = ScriptedCollector([
        [_disk(40.0)],   # tick 1: healthy
        [_disk(8.0)],    # tick 2: crosses threshold -> notable
        [_disk(7.5)],    # tick 3: still low -> silent (hysteresis)
        [_disk(40.0)],   # tick 4: recovered -> clears
        [_disk(9.0)],    # tick 5: crosses again -> notable
    ])
    perception = Perception(
        collectors=[collector],
        thresholds=PerceptionThresholds(low_disk_gb=15.0),
    )

    fired = [len(perception.sense()[0]) for _ in range(5)]

    assert fired == [0, 1, 0, 0, 1]


def test_disk_step_drop_refires_while_still_low():
    collector = ScriptedCollector([
        [_disk(14.0)],   # tick 1: already low -> notable
        [_disk(6.0)],    # tick 2: dropped >5 GB -> notable again
    ])
    perception = Perception(
        collectors=[collector],
        thresholds=PerceptionThresholds(low_disk_gb=15.0, disk_step_gb=5.0),
    )

    assert len(perception.sense()[0]) == 1
    assert len(perception.sense()[0]) == 1


def test_firewall_turning_off_is_critical():
    collector = ScriptedCollector([[_fw(True)], [_fw(False)]])
    perception = Perception(collectors=[collector])

    assert perception.sense()[0] == []
    notables = perception.sense()[0]
    assert len(notables) == 1
    assert notables[0].severity == "critical"


def test_new_listening_port_is_notable():
    collector = ScriptedCollector([
        [_ports(["80/nginx", "443/nginx"])],
        [_ports(["80/nginx", "443/nginx", "3389/svchost"])],
    ])
    perception = Perception(collectors=[collector])

    assert perception.sense()[0] == []
    notables = perception.sense()[0]
    assert len(notables) == 1
    assert "3389/svchost" in notables[0].summary


def test_broken_collector_does_not_crash_perception():
    class Broken(SignalCollector):
        name = "broken"

        def collect(self):
            raise RuntimeError("boom")

    perception = Perception(collectors=[Broken()])
    notables, observations = perception.sense()

    assert notables == []
    assert observations == []
