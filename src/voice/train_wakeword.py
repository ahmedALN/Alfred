"""
Custom "Hey Alfred" wake word.

Full local training needs PyTorch + piper-sample-generator + a fair bit
of time, so this script does the part that must happen on your machine -
recording real samples of your voice - and then either trains locally
(if the heavy deps are present) or hands you everything you need for the
openWakeWord Colab notebook.

    python -m src.voice.train_wakeword record      # capture ~30 samples
    python -m src.voice.train_wakeword train       # local training (needs torch)
    python -m src.voice.train_wakeword status

Until a model exists at models/alfred.onnx, Alfred listens for
"hey jarvis" instead.
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
_TARGET_SAMPLES = 30
_RATE = 16_000


def _record_one(path: Path, seconds: float = 2.0) -> None:
    import sounddevice as sd

    frames = sd.rec(
        int(seconds * _RATE), samplerate=_RATE, channels=1, dtype="int16"
    )
    sd.wait()

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_RATE)
        wf.writeframes(frames.tobytes())


def record() -> int:
    _SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(_SAMPLE_DIR.glob("*.wav"))
    start = len(existing)

    if start >= _TARGET_SAMPLES:
        print(f"{start} samples already recorded in {_SAMPLE_DIR}.")
        return 0

    print(
        f"Recording {_TARGET_SAMPLES - start} more samples of \"{_PHRASE}\".\n"
        "Say it naturally each time - vary your tone, distance, and speed.\n"
        "Press Enter to record each 2-second clip (Ctrl+C to stop).\n"
    )

    for i in range(start, _TARGET_SAMPLES):
        try:
            input(f"  [{i + 1}/{_TARGET_SAMPLES}] Enter, then say it: ")
        except (KeyboardInterrupt, EOFError):
            print("\nStopped.")
            break

        path = _SAMPLE_DIR / f"alfred_{i:03d}.wav"
        time.sleep(0.2)
        _record_one(path)
        print(f"      saved {path.name}")

    count = len(list(_SAMPLE_DIR.glob("*.wav")))
    print(f"\n{count} samples in {_SAMPLE_DIR}.")
    return count


def train() -> int:
    try:
        import torch  # noqa: F401
        from openwakeword import train  # noqa: F401
    except Exception:  # noqa: BLE001
        print(
            "Local training needs PyTorch and the openWakeWord training extras:\n"
            "  pip install torch openwakeword[train] piper-sample-generator\n\n"
            "Easier route - the official Colab notebook:\n"
            "  https://github.com/dscripka/openWakeWord (see 'Training new models')\n"
            f"  Upload the .wav files from {_SAMPLE_DIR} as your positive samples,\n"
            f"  train, download the .onnx, and save it as:\n    {_MODEL_PATH}\n"
        )
        return 1

    samples = sorted(_SAMPLE_DIR.glob("*.wav"))
    if len(samples) < 10:
        print(f"Need at least 10 recorded samples first: python -m src.voice.train_wakeword record")
        return 1

    print(
        "Local training is not automated here yet - it is long and "
        "hardware-heavy. Use the Colab notebook with your recorded samples; "
        "it takes ~20 minutes and produces a better model.\n"
        f"Samples ready at: {_SAMPLE_DIR}\n"
        f"Save the result to: {_MODEL_PATH}"
    )
    return 0


def status() -> int:
    n = len(list(_SAMPLE_DIR.glob("*.wav"))) if _SAMPLE_DIR.exists() else 0
    print(f"Recorded samples: {n}/{_TARGET_SAMPLES}")
    if _MODEL_PATH.exists():
        print(f"Custom model:     {_MODEL_PATH}  (set ALFRED_WAKE_MODEL to use it)")
    else:
        print("Custom model:     not built - Alfred uses 'hey jarvis' for now")
    return 0


def main(argv: list[str]) -> int:
    action = argv[0] if argv else "status"
    return {
        "record": record,
        "train": train,
        "status": status,
    }.get(action, lambda: (print(__doc__), 2)[1])()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
