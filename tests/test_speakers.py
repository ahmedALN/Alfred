"""Alfred was coming out of the monitor.

Two separate questions, and conflating them is what made this subtle.

WHICH SPEAKER is a Windows setting, and only WASAPI reflects it -
sounddevice's own default is MME's, fixed when the audio stack was
enumerated. This machine has twenty output endpoints across four host
APIs, several of them the same speakers under different names.

HOW TO PLAY THROUGH IT is a different matter: WASAPI in shared mode
will not resample, the device is 48kHz native, and Alfred's voice is
24kHz - so opening it on WASAPI fails with "Invalid sample rate" and
Alfred says nothing at all. Picking the right speaker on the wrong API
is worse than the bug it fixes.
"""

from __future__ import annotations

import pytest

import src.voice.speakers as speakers

# The real device table off this machine, trimmed.
DEVICES = [
    {"name": "Microsoft Sound Mapper - Output", "max_output_channels": 2, "hostapi": 0},
    {"name": "Speakers (Realtek(R) Audio)", "max_output_channels": 2, "hostapi": 0},
    {"name": "Digital Audio (S/PDIF) (Realtek", "max_output_channels": 2, "hostapi": 0},
    {"name": "Speakers (Realtek(R) Audio)", "max_output_channels": 2, "hostapi": 1},
    {"name": "Speakers (Realtek(R) Audio)", "max_output_channels": 2, "hostapi": 2},
    {"name": "Speakers (HD Audio Speaker)", "max_output_channels": 2, "hostapi": 3},
]

HOSTAPIS = [
    {"name": "MME", "default_output_device": 1},
    {"name": "Windows DirectSound", "default_output_device": 3},
    {"name": "Windows WASAPI", "default_output_device": 4},
    {"name": "Windows WDM-KS", "default_output_device": 5},
]


@pytest.fixture()
def audio(monkeypatch):
    """WASAPI (hostapi 2) takes 48000 only. MME resamples anything."""

    def check(device, samplerate, channels, dtype):
        api = DEVICES[device]["hostapi"]
        if api in (2, 3) and samplerate != 48000:
            raise ValueError("Invalid sample rate [PaErrorCode -9997]")

    fake = type("sd", (), {
        "query_devices": staticmethod(
            lambda index=None: DEVICES if index is None else DEVICES[index]
        ),
        "query_hostapis": staticmethod(
            lambda index=None: HOSTAPIS if index is None else HOSTAPIS[index]
        ),
        "check_output_settings": staticmethod(check),
    })

    monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake)
    monkeypatch.delenv("ALFRED_AUDIO_OUTPUT", raising=False)
    return fake


def test_the_speaker_windows_is_set_to_is_the_one_used(audio):
    """WASAPI's default names it, whatever MME thinks."""
    assert speakers._windows_default_name() == "Speakers (Realtek(R) Audio)"


def test_it_is_opened_on_an_api_that_can_carry_the_stream(audio):
    """The bug this would otherwise have introduced: right speaker,
    wrong API, and Alfred silent."""
    picked = speakers.chosen_output(samplerate=24000)

    assert DEVICES[picked]["name"] == "Speakers (Realtek(R) Audio)"
    assert HOSTAPIS[DEVICES[picked]["hostapi"]]["name"] == "MME"


def test_at_the_native_rate_it_is_still_the_same_speaker(audio):
    picked = speakers.chosen_output(samplerate=48000)

    assert DEVICES[picked]["name"] == "Speakers (Realtek(R) Audio)"


def test_an_index_in_the_config_wins(audio, monkeypatch):
    monkeypatch.setenv("ALFRED_AUDIO_OUTPUT", "5")

    assert speakers.chosen_output(samplerate=24000) == 5


def test_a_name_in_the_config_wins(audio, monkeypatch):
    """By name, so a config written once survives the indices moving
    about - which they do whenever something is plugged in."""
    monkeypatch.setenv("ALFRED_AUDIO_OUTPUT", "HD Audio Speaker")

    picked = speakers.chosen_output(samplerate=48000)

    assert DEVICES[picked]["name"] == "Speakers (HD Audio Speaker)"


def test_a_name_that_matches_nothing_falls_back_rather_than_failing(
    audio, monkeypatch, capsys
):
    monkeypatch.setenv("ALFRED_AUDIO_OUTPUT", "a speaker that is not here")

    assert speakers.chosen_output(samplerate=24000) is None
    assert "no output device matching" in capsys.readouterr().out


def test_no_api_taking_the_rate_falls_back_to_the_system_default(
    audio, monkeypatch, capsys
):
    """Silence is the one outcome worse than the wrong speaker."""
    monkeypatch.setenv("ALFRED_AUDIO_OUTPUT", "HD Audio Speaker")

    assert speakers.chosen_output(samplerate=24000) is None
    assert "accepts 24000Hz" in capsys.readouterr().out


def test_asking_without_a_rate_does_not_check_one(audio):
    """The lister wants the speaker, not a playable stream."""
    assert speakers.chosen_output() is not None


def test_mme_is_preferred_because_it_resamples():
    assert speakers._api_rank("MME") < speakers._api_rank("Windows WASAPI")
    assert speakers._api_rank("Windows DirectSound") < speakers._api_rank(
        "Windows WDM-KS"
    )
