"""The check that says whether Alfred is in working order.

`src.status` says what Alfred is doing; the doctor says whether the
parts are sound. It exists because several things were not sound and
nothing said so - three collectors ran for days with nothing reading
them, and the command gate called seven destructive one-liners
ordinary.

A doctor that can be wrong in the reassuring direction is worse than no
doctor, so these tests are mostly about it failing when it should.
"""

from __future__ import annotations

import src.doctor as doctor
from src.doctor import BAD, OK, WARN, Section, check_brain, check_safety, check_tools


def states(section: Section) -> dict[str, str]:
    return {c.name: c.state for c in section.checks}


# ====================================================================
# It reports the truth today
# ====================================================================


def test_the_brain_section_passes_now_that_readings_are_read():
    section = check_brain()

    assert states(section)["every reading reaches a check"] == OK
    assert states(section)["it can notice something about you"] == OK
    assert states(section)["an overdue item becomes a notable"] == OK


def test_the_safety_section_passes_now_the_gate_is_hardened():
    section = check_safety()

    assert states(section)["destructive spellings are caught"] == OK
    assert states(section)["ordinary commands still just run"] == OK
    assert states(section)["other people's words arrive as data"] == OK


def test_the_tools_section_passes_now_labels_are_read_past():
    section = check_tools()

    assert states(section)["calls are read past the label"] == OK
    assert states(section)["an empty call is still refused"] == OK


def test_every_section_runs_without_raising():
    for name, builder in doctor.SECTIONS.items():
        section = builder()
        assert section.checks, f"{name} produced no checks at all"


# ====================================================================
# It notices when things break
# ====================================================================


def test_the_brain_check_fails_if_a_reading_stops_being_read(monkeypatch):
    """The exact regression it exists to catch."""
    import src.brain.perception as perception

    real = perception._is_handled

    def blind(key):
        return False if key.startswith("world.") else real(key)

    monkeypatch.setattr(perception, "_is_handled", blind)

    section = check_brain()

    assert states(section)["every reading reaches a check"] == BAD


def test_the_safety_check_fails_if_the_gate_softens(monkeypatch):
    import src.brain.policy as policy

    monkeypatch.setattr(policy, "classify_command", lambda _c: "ordinary")

    section = check_safety()

    assert states(section)["destructive spellings are caught"] == BAD


def test_the_safety_check_fails_if_the_gate_gets_paranoid(monkeypatch):
    """Stopping everything is its own failure - a gate like that gets
    turned off, and then it stops nothing."""
    import src.brain.policy as policy

    monkeypatch.setattr(policy, "classify_command", lambda _c: "catastrophic")

    section = check_safety()

    assert states(section)["ordinary commands still just run"] == BAD


def test_the_tools_check_fails_if_the_argument_layer_regresses(monkeypatch):
    import src.tools.arguments as arguments

    monkeypatch.setattr(arguments, "normalise_open_app", lambda args: args)

    section = check_tools()

    assert states(section)["calls are read past the label"] == BAD


def test_the_tools_check_fails_if_a_call_gets_guessed_at(monkeypatch):
    """Inventing an app name from an empty call is the opposite failure."""
    import src.tools.arguments as arguments

    monkeypatch.setattr(
        arguments, "normalise_open_app", lambda args: {**args, "app": "Notepad"}
    )

    section = check_tools()

    assert states(section)["an empty call is still refused"] == BAD


# ====================================================================
# The shape of the report
# ====================================================================


def test_a_section_takes_the_worst_state_it_holds():
    section = Section("x")
    section.add("a", OK)
    assert section.worst == OK

    section.add("b", WARN)
    assert section.worst == WARN

    section.add("c", BAD)
    assert section.worst == BAD


def test_a_check_that_itself_explodes_is_reported_not_swallowed(monkeypatch, capsys):
    monkeypatch.setitem(
        doctor.SECTIONS, "brain",
        lambda: (_ for _ in ()).throw(RuntimeError("the check is broken")),
    )

    code = doctor.run(["brain"])

    assert code == 1
    assert "check itself failed" in capsys.readouterr().out


def test_the_exit_code_is_usable_from_a_scheduled_task(monkeypatch):
    monkeypatch.setitem(
        doctor.SECTIONS, "brain", lambda: Section("brain", []),
    )
    assert doctor.run(["brain"]) == 0

    monkeypatch.setitem(
        doctor.SECTIONS, "brain",
        lambda: Section("brain", [doctor.Check("x", BAD, "broken")]),
    )
    assert doctor.run(["brain"]) == 1

    # A warning is worth reading and is not a failure.
    monkeypatch.setitem(
        doctor.SECTIONS, "brain",
        lambda: Section("brain", [doctor.Check("x", WARN, "worth knowing")]),
    )
    assert doctor.run(["brain"]) == 0


def test_quiet_mode_hides_what_is_working(monkeypatch, capsys):
    monkeypatch.setitem(
        doctor.SECTIONS, "brain",
        lambda: Section("brain", [
            doctor.Check("fine", OK, "nothing to see"),
            doctor.Check("not fine", BAD, "everything to see"),
        ]),
    )

    doctor.run(["brain"], quiet=True)
    out = capsys.readouterr().out

    assert "nothing to see" not in out
    assert "everything to see" in out


def test_an_unknown_section_says_what_there_is(capsys):
    assert doctor.run(["nonsense"]) == 2
    assert "brain" in capsys.readouterr().out
