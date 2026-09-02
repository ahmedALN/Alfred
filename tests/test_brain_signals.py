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


def test_firewall_off_needs_two_consecutive_readings():
    collector = ScriptedCollector([
        [_fw(True)],    # on
        [_fw(False)],   # one off reading - could be a glitch, stay quiet
        [_fw(False)],   # confirmed off -> critical
        [_fw(False)],   # still off - hysteresis, quiet
    ])
    perception = Perception(collectors=[collector])

    fired = [len(perception.sense()[0]) for _ in range(4)]
    assert fired == [0, 0, 1, 0]


def test_single_off_glitch_does_not_alarm():
    collector = ScriptedCollector([
        [_fw(True)], [_fw(False)], [_fw(True)],  # blip back to on
    ])
    perception = Perception(collectors=[collector])
    assert [len(perception.sense()[0]) for _ in range(3)] == [0, 0, 0]


def test_a_port_that_stays_open_is_notable():
    """Reported on the second sighting, not the first.

    A port seen once was an app starting up. Saying so was 148 of the
    brain's 210 notables - seven times everything else put together,
    and none of it anything anybody wanted told.
    """
    collector = ScriptedCollector([
        [_ports(["80/nginx", "443/nginx"])],
        [_ports(["80/nginx", "443/nginx", "3389/svchost"])],
        [_ports(["80/nginx", "443/nginx", "3389/svchost"])],
    ])
    perception = Perception(collectors=[collector])

    assert perception.sense()[0] == []
    assert perception.sense()[0] == []      # seen once: wait and see

    notables = perception.sense()[0]
    assert len(notables) == 1
    assert "3389/svchost" in notables[0].summary


def test_a_port_that_came_and_went_is_never_mentioned():
    collector = ScriptedCollector([
        [_ports(["80/nginx"])],
        [_ports(["80/nginx", "57621/Spotify"])],   # Spotify starting
        [_ports(["80/nginx"])],                     # and settled
        [_ports(["80/nginx"])],
    ])
    perception = Perception(collectors=[collector])

    assert [len(perception.sense()[0]) for _ in range(4)] == [0, 0, 0, 0]


def test_parse_enabled_never_guesses_off():
    from src.brain.signals import _parse_enabled

    assert _parse_enabled(1) is True
    assert _parse_enabled(0) is False
    assert _parse_enabled(True) is True
    assert _parse_enabled("True") is True
    assert _parse_enabled("Disabled") is False
    # anything ambiguous -> None (unknown), NOT False
    assert _parse_enabled(None) is None
    assert _parse_enabled(2) is None
    assert _parse_enabled("NotConfigured") is None
    assert _parse_enabled({}) is None


def test_broken_collector_does_not_crash_perception():
    class Broken(SignalCollector):
        name = "broken"

        def collect(self):
            raise RuntimeError("boom")

    perception = Perception(collectors=[Broken()])
    notables, observations = perception.sense()

    assert notables == []
    assert observations == []
