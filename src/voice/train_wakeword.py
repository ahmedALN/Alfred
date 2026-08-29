"""
Custom "Hey Alfred" wake word.

The easy path (recommended) - no training, ~40 MB:

    python -m src.voice.setup_wakeword
    # then in .env:  ALFRED_WAKE_PHRASE=hey alfred

That uses Vosk keyword spotting with a constrained grammar. Works for
any short phrase.

The harder path - a dedicated openWakeWord model (better rejection of
similar phrases, needs training):

    python -m src.voice.train_wakeword record    # ~30 samples of your voice
    # then train it in the openWakeWord Colab notebook with those clips
    # and save the result as models/alfred.onnx  ->  ALFRED_WAKE_MODEL=models/alfred.onnx
"""

from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_SAMPLE_DIR = _ROOT / "models" / "alfred_samples"
_MODEL_PATH = _ROOT / "models" / "alfred.onnx"
_PHRASE = "Hey Alfred"
_TARGET = 30
_RATE = 16_000


def _record_one(path: Path, seconds: float = 2.0) -> None:
    import sounddevice as sd

    frames = sd.rec(int(seconds * _RATE), samplerate=_RATE, channels=1, dtype="int16")
    sd.wait()
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_RATE)
        wf.writeframes(frames.tobytes())


def record() -> int:
    _SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    start = len(sorted(_SAMPLE_DIR.glob("*.wav")))
    if start >= _TARGET:
        print(f"{start} samples already in {_SAMPLE_DIR}.")
        return 0

    print(
        f"Recording {_TARGET - start} clips of \"{_PHRASE}\". Vary tone, "
        "distance, speed. Enter to record each (Ctrl+C to stop).\n"
    )
    for i in range(start, _TARGET):
        try:
            input(f"  [{i + 1}/{_TARGET}] Enter, then say it: ")
        except (KeyboardInterrupt, EOFError):
            break
        time.sleep(0.2)
        _record_one(_SAMPLE_DIR / f"alfred_{i:03d}.wav")
        print("      saved")

    n = len(sorted(_SAMPLE_DIR.glob("*.wav")))
    print(
        f"\n{n} samples in {_SAMPLE_DIR}. Train at the openWakeWord Colab "
        "notebook, save the .onnx as:\n  " + str(_MODEL_PATH)
    )
    return n


def status() -> int:
    n = len(sorted(_SAMPLE_DIR.glob("*.wav"))) if _SAMPLE_DIR.exists() else 0
    vosk = (_ROOT / "models" / "vosk-model-small-en-us-0.15").exists()
    print(f"Vosk phrase model : {'installed' if vosk else 'not installed'}"
          f"  (python -m src.voice.setup_wakeword)")
    print(f"openWakeWord model: {'built' if _MODEL_PATH.exists() else 'not built'}")
    print(f"recorded samples  : {n}/{_TARGET}")
    return 0


def main(argv: list[str]) -> int:
    return {"record": record, "status": status}.get(
        argv[0] if argv else "status", lambda: (print(__doc__), 2)[1]
    )()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
