"""Noticing the mailbox link has lapsed, before you do.

Alfred handles the expiry correctly when asked - it says what to run -
but only when asked. The failure mode was an inbox that quietly stopped
being mentioned, so a week of "nothing important came in" would have
been a week of not looking.
"""

from src.brain.mailwatch import MailCollector


class _Mail:
    def __init__(self, linked=True, address="me@example.com", broken=False):
        self.linked = linked
        self._address = address
        self.broken = broken
        self.asked = 0

    def address(self, refresh=False):
        self.asked += 1
        if self.broken:
            raise RuntimeError("the mailbox link has expired")
        return self._address


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_a_working_mailbox_is_reported_as_working():
    seen = MailCollector(_Mail(), clock=_Clock()).collect()

    assert seen[0].value is True
    assert "me@example.com" in seen[0].summary


def test_a_lapsed_link_says_what_to_run():
    seen = MailCollector(_Mail(broken=True), clock=_Clock()).collect()

    assert seen[0].value is False
    assert "python -m src.workspace link" in seen[0].summary


def test_a_mailbox_that_was_never_set_up_is_not_nagged_about():
    """Never linked is a thing not set up, not a thing gone wrong."""
    assert MailCollector(_Mail(linked=False), clock=_Clock()).collect() == []


def test_no_mailbox_at_all_is_quiet():
    assert MailCollector(None, clock=_Clock()).collect() == []


def test_it_does_not_ask_google_every_ninety_seconds():
    """The link lasts a week. Checking it each tick would be a network
    call an hour to watch something that changes on the scale of days."""
    mail, clock = _Mail(), _Clock()
    collector = MailCollector(mail, clock=clock)

    for _ in range(20):
        collector.collect()
        clock.t += 90

    assert mail.asked == 1


def test_it_does_check_again_eventually():
    mail, clock = _Mail(), _Clock()
    collector = MailCollector(mail, clock=clock)

    collector.collect()
    clock.t += 31 * 60
    collector.collect()

    assert mail.asked == 2


def test_the_answer_between_checks_is_the_last_one():
    mail, clock = _Mail(), _Clock()
    collector = MailCollector(mail, clock=clock)

    first = collector.collect()
    clock.t += 90
    again = collector.collect()

    assert again[0].summary == first[0].summary
