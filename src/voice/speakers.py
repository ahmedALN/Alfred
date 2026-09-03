"""
Which speaker Alfred talks out of.

    python -m src.voice.speakers            list them, and say which is in use
    python -m src.voice.speakers test       say something through it
    python -m src.voice.speakers test 19    ...through that one

sounddevice picks its own default, and on Windows that is the MME
default - a fixed device chosen when the audio stack was enumerated,
not the one you set in Settings. This machine has twenty output
endpoints across four host APIs, several of them the same speakers
under different names and one of them a monitor, so "the default"
picked by index alone is a coin toss. Alfred was coming out of the
monitor.

Two separate questions, and conflating them is what made this subtle.
WHICH SPEAKER is a Windows setting and only WASAPI reflects it. HOW TO
PLAY THROUGH IT is a different matter: WASAPI in shared mode will not
resample, this device is 48kHz native, and Alfred's voice is 24kHz - so
opening it on WASAPI fails with "Invalid sample rate" and Alfred says
nothing at all. MME resamples happily.

So WASAPI names the speaker and MME carries the stream to it.
`ALFRED_AUDIO_OUTPUT` overrides the choice - an index, or any part of a
device name.
"""

from __future__ import annotations

import os
import sys


def _devices() -> list[dict]:
    import sounddevice as sd

    return list(sd.query_devices())


def _windows_default_name() -> str:
    """The speaker Windows is set to, by name.

    Asked through WASAPI because that is the API tracking the setting -
    but only for the NAME. WASAPI is not where the audio gets played:
    see `chosen_output`.
    """

    import sounddevice as sd

    for info in sd.query_hostapis():
        if info["name"] != "Windows WASAPI":
            continue

        index = info.get("default_output_device", -1)

        if index is not None and index >= 0:
            try:
                return str(sd.query_devices(index)["name"])
            except Exception:  # noqa: BLE001
                return ""

    return ""


def chosen_output(samplerate: int | None = None, channels: int = 1) -> int | None:
    """The device Alfred should speak through, or None for the default.

    Two separate questions, and conflating them is what made this
    subtle. WHICH SPEAKER is a Windows setting, and only WASAPI
    reflects it - sounddevice's own default is MME's, fixed when the
    audio stack was enumerated, and on this machine that is one of
    twenty endpoints picked by index. Alfred came out of the monitor.

    HOW TO PLAY THROUGH IT is a different matter, because WASAPI in
    shared mode will not resample: this device is 48kHz native and
    Gemini's voice is 24kHz, so opening it on WASAPI fails outright
    with "Invalid sample rate" and Alfred says nothing at all. MME and
    DirectSound resample happily.

    So WASAPI is asked which speaker, and the stream is opened on
    whichever host API can actually carry it.
    """

    import sounddevice as sd

    wanted = os.getenv("ALFRED_AUDIO_OUTPUT", "").strip()

    if wanted.isdigit():
        return int(wanted)

    name = wanted or _windows_default_name()

    if not name:
        return None

    low = name.lower()
    matches = [
        index for index, device in enumerate(_devices())
        if device["max_output_channels"] > 0 and low in device["name"].lower()
    ]

    if not matches:
        if wanted:
            print(f"[Speaker] no output device matching {wanted!r}; using default")
        return None

    # If the default would land on this speaker anyway, say nothing and
    # let it.
    #
    # This is not tidiness. Passing device=None lets PortAudio resolve
    # the default when the stream opens; passing an index binds to an
    # entry from enumeration time, and a stale one opens cleanly and
    # then fails every write with "There is no driver installed on your
    # system" [MME error 6]. Alfred answered four questions in a row in
    # silence that way - and the index it was pinned to was the SAME
    # speaker device=None had been using for months.
    #
    # So the override is spent only where it buys something: when the
    # speaker Windows is set to is not the one sounddevice would pick.
    if not wanted and _default_is_already(name):
        return None

    if samplerate is None:
        return matches[0]

    # In host-API preference order, the first that will take the stream.
    ordered = sorted(
        matches,
        key=lambda i: _api_rank(sd.query_hostapis(_devices()[i]["hostapi"])["name"]),
    )

    for index in ordered:
        try:
            sd.check_output_settings(
                device=index, samplerate=samplerate,
                channels=channels, dtype="int16",
            )
            return index
        except Exception:  # noqa: BLE001
            continue

    print(
        f"[Speaker] no host API for {name!r} accepts {samplerate}Hz; "
        "using the system default"
    )
    return None


def _default_is_already(name: str) -> bool:
    """Would sounddevice's own default land on this speaker anyway?"""

    import sounddevice as sd

    try:
        index = sd.default.device[1]

        if index is None or index < 0:
            return False

        return name.lower() in str(sd.query_devices(index)["name"]).lower()
    except Exception:  # noqa: BLE001
        return False


def _api_rank(api: str) -> int:
    """Which host API to try first.

    MME leads here on purpose, and only here: it is the one that
    resamples, so it is the one that can actually carry a 24kHz stream
    to a 48kHz device. WASAPI has already done its job by the time this
    is asked - it named the speaker.
    """
    order = ("MME", "Windows DirectSound", "Windows WASAPI", "Windows WDM-KS")

    return order.index(api) if api in order else len(order)


def describe(index: int | None) -> str:
    import sounddevice as sd

    if index is None:
        return "sounddevice default"

    try:
        device = sd.query_devices(index)
        api = sd.query_hostapis(device["hostapi"])["name"]
        return f"{device['name']} ({api})"
    except Exception:  # noqa: BLE001
        return f"device {index}"


def _list() -> int:
    import sounddevice as sd

    picked = chosen_output(samplerate=24000)

    print("Output devices")
    print("=" * 74)

    for index, device in enumerate(_devices()):
        if device["max_output_channels"] <= 0:
            continue

        api = sd.query_hostapis(device["hostapi"])["name"]
        mark = "  <-- Alfred uses this" if index == picked else ""
        print(f"  {index:3}  {device['name'][:40]:42} {api:20}{mark}")

    print()
    print(f"  in use: {describe(picked)}")
    print()
    print("  To pin one, put its number or any part of its name in .env:")
    print("      ALFRED_AUDIO_OUTPUT=Realtek")
    print("  Then: python -m src.voice.speakers test")

    return 0


def _test(index: int | None) -> int:
    """A second of tone, so you can hear which speaker it came from."""
    import numpy as np
    import sounddevice as sd

    rate = 24000     # the rate Alfred's voice actually is

    if index is None:
        index = chosen_output(samplerate=rate)

    print(f"playing a tone through: {describe(index)}")
    t = np.linspace(0, 1.0, rate, endpoint=False)
    tone = (0.25 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

    try:
        sd.play(tone, rate, device=index)
        sd.wait()
    except Exception as exc:  # noqa: BLE001
        print(f"  failed: {exc}")
        return 1

    print("  done - if that came from the wrong speaker, pick another number")
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "test":
        return _test(int(argv[1]) if len(argv) > 1 else None)

    return _list()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
