from src import childsession as cs


def test_report_separates_blockers_from_warnings():
    r = cs.Report()
    r.add("fine", cs.OK, "all good")
    r.add("iffy", cs.WARN, "worth knowing")
    r.add("broken", cs.FAIL, "cannot proceed", fix="do this")
    r.add("note", cs.INFO, "fyi")

    assert [c.name for c in r.blockers] == ["broken"]
    assert [c.name for c in r.warnings] == ["iffy"]
    assert len(r.checks) == 4


def test_every_state_has_a_display_mark():
    for state in (cs.OK, cs.WARN, cs.FAIL, cs.INFO):
        assert state in cs._MARK


def test_wrap_never_exceeds_the_width():
    text = ("Child sessions need Pro, Enterprise or Education. Home has no "
            "Remote Desktop host, so this approach cannot work here.")
    for line in cs._wrap(text, 40):
        assert len(line) <= 40
    assert " ".join(cs._wrap(text, 40)) == text


def test_wrap_handles_short_and_empty_text():
    assert cs._wrap("", 20) == []
    assert cs._wrap("short", 20) == ["short"]


def test_probe_runs_without_raising_and_covers_every_check():
    """The probe is read-only; a broken individual check must not kill it."""
    report = cs.run_probe()
    assert len(report.checks) >= len(cs.ALL_CHECKS)
    assert all(c.state in cs._MARK for c in report.checks)


def test_a_crashing_check_is_recorded_not_raised(monkeypatch):
    def boom(_r):
        raise RuntimeError("nope")

    monkeypatch.setattr(cs, "ALL_CHECKS", (boom,))
    report = cs.run_probe()
    assert len(report.checks) == 1
    assert report.checks[0].state == cs.WARN
    assert "nope" in report.checks[0].detail


def test_cli_rejects_unknown_commands():
    assert cs.main(["nonsense"]) == 2
    assert cs.main([]) == 0


def test_explain_mentions_how_to_undo_it():
    assert "TO UNDO IT" in cs._EXPLAIN
    assert "WTSEnableChildSessions" in cs._EXPLAIN
