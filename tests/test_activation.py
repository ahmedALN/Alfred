import asyncio

from src.voice.activation import ActivationController
from src.voice.hotkey import parse_hotkey


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def tick(self, dt):
        self.t += dt


# ---------------------------------------------------------------- activation


def test_starts_asleep_when_wake_gated():
    a = ActivationController(idle_seconds=30, always_on=False)
    assert a.is_listening is False


def test_always_on_is_always_listening():
    a = ActivationController(always_on=True)
    assert a.is_listening is True
    a.sleep()
    assert a.is_listening is True


def test_wake_then_idle_timeout():
    clk = Clock()
    a = ActivationController(idle_seconds=30, monotonic=clk)
    states = []
    a.on_state_change = states.append

    a.wake()
    assert a.is_listening is True
    assert states == [True]

    async def drive():
        task = asyncio.create_task(a.run(poll_seconds=0.01))
        clk.tick(10)
        await asyncio.sleep(0.05)
        assert a.is_listening is True  # activity within window
        a.note_activity()
        clk.tick(20)
        await asyncio.sleep(0.05)
        assert a.is_listening is True  # reset by note_activity
        clk.tick(31)
        await asyncio.sleep(0.05)
        assert a.is_listening is False
        task.cancel()

    asyncio.run(drive())
    assert states == [True, False]


def test_wake_while_listening_is_noop_for_callback():
    a = ActivationController()
    states = []
    a.on_state_change = states.append
    a.wake()
    a.wake()
    assert states == [True]


# ---------------------------------------------------------------- hotkey parse


def test_parse_hotkey_combos():
    mods, vk = parse_hotkey("ctrl+alt+a")
    assert mods & 0x0002 and mods & 0x0001  # MOD_CONTROL | MOD_ALT
    assert vk == ord("A")

    mods, vk = parse_hotkey("win+shift+f5")
    assert mods & 0x0008 and mods & 0x0004
    assert vk == 0x74


def test_parse_hotkey_rejects_garbage():
    assert parse_hotkey("") is None
    assert parse_hotkey("ctrl+alt") is None  # no key
    assert parse_hotkey("bogus+x") is None
