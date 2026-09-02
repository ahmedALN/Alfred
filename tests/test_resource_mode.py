import asyncio

from src.resource_mode import GAME, NORMAL, ResourceMode


class FakeProvider:
    def __init__(self, name):
        self.name = name
        self.unloaded = 0

    def unload(self):
        self.unloaded += 1


class FakeProviders:
    def __init__(self):
        self.chat = FakeProvider("chat")
        self.vision = FakeProvider("vision")
        self.embedder = FakeProvider("embed")


class FakeBrain:
    def __init__(self):
        self.paused = False

    def set_paused(self, v):
        self.paused = v


class FakeQueue:
    def __init__(self):
        self.paused = False

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False


class FakeChild:
    def __init__(self):
        self.capture_stopped = 0

    def capture_stop(self):
        self.capture_stopped += 1


def _rm(**kw):
    spoken = []

    async def speak(t):
        spoken.append(t)

    providers = FakeProviders()
    brain = FakeBrain()
    queue = FakeQueue()
    child = FakeChild()
    rm = ResourceMode(
        providers=providers,
        speak=speak,
        brain=brain,
        task_queue=queue,
        child_client=child,
        autodetect=kw.get("autodetect", False),
        detect_seconds=kw.get("detect_seconds", 1.0),
        clear_seconds=kw.get("clear_seconds", 1.0),
        fullscreen_probe=kw.get("fullscreen_probe", lambda: False),
        foreground_probe=kw.get("foreground_probe", lambda: "game.exe"),
        monotonic=kw.get("monotonic") or (lambda: 0.0),
    )
    return rm, providers, brain, queue, child, spoken


def test_enter_game_frees_resources():
    rm, providers, brain, queue, child, spoken = _rm()

    asyncio.run(rm.enter_game("user"))

    assert rm.state == GAME
    assert brain.paused is True
    assert queue.paused is True
    assert child.capture_stopped == 1
    assert providers.chat.unloaded == 1
    assert providers.vision.unloaded == 1
    assert providers.embedder.unloaded == 1
    assert spoken and "freed up the GPU" in spoken[0]


def test_exit_game_restores():
    rm, _providers, brain, queue, _child, _spoken = _rm()
    asyncio.run(rm.enter_game("user"))
    asyncio.run(rm.exit_game("user"))

    assert rm.state == NORMAL
    assert brain.paused is False
    assert queue.paused is False


def test_enter_is_idempotent():
    rm, providers, *_ = _rm()
    asyncio.run(rm.enter_game("user"))
    asyncio.run(rm.enter_game("user"))
    assert providers.chat.unloaded == 1  # not doubled


def test_autodetect_engages_on_sustained_fullscreen_game():
    clock = [0.0]
    fs = [False]

    rm, _providers, _brain, *_ = _rm(
        autodetect=True,
        detect_seconds=25,
        clear_seconds=10,
        fullscreen_probe=lambda: fs[0],
        foreground_probe=lambda: "eldenring.exe",
        monotonic=lambda: clock[0],
    )

    async def drive():
        task = asyncio.create_task(rm.run(poll_seconds=0.01))
        fs[0] = True
        clock[0] = 5
        await asyncio.sleep(0.05)
        assert rm.state == NORMAL  # not long enough yet
        clock[0] = 40
        await asyncio.sleep(0.05)
        assert rm.state == GAME  # auto-engaged
        fs[0] = False
        clock[0] = 60
        await asyncio.sleep(0.05)   # clear_since gets set to 60
        clock[0] = 75               # ... now enough time has passed
        await asyncio.sleep(0.05)
        assert rm.state == NORMAL  # auto-disengaged
        task.cancel()

    asyncio.run(drive())


def test_autodetect_ignores_fullscreen_browser():
    rm, *_ = _rm(
        autodetect=True,
        fullscreen_probe=lambda: True,
        foreground_probe=lambda: "chrome.exe",
    )
    assert rm._looks_like_a_game() is False
