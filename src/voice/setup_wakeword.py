"""
python -m src.voice.setup_wakeword

Downloads the small Vosk model so Alfred can listen for a custom wake
phrase ("Hey Alfred") with no training. Set ALFRED_WAKE_PHRASE in .env
to use it; leave it blank to keep the bundled "Hey Jarvis".
"""

from __future__ import annotations

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_MODELS = _ROOT / "models"
_NAME = "vosk-model-small-en-us-0.15"
_URL = f"https://alphacephei.com/vosk/models/{_NAME}.zip"


def main() -> int:
    target = _MODELS / _NAME
    if target.exists():
        print(f"[ok] Vosk model already present at {target}")
        return 0

    _MODELS.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {_NAME} (~40 MB) ...")

    try:
        with urllib.request.urlopen(_URL, timeout=120) as resp:
            data = resp.read()
    except Exception as exc:  # noqa: BLE001
        print(f"download failed: {exc}")
        print(f"Get it manually from {_URL} and unzip into {_MODELS}/")
        return 1

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(_MODELS)

    if target.exists():
        print(f"[ok] installed to {target}")
        print('Set  ALFRED_WAKE_PHRASE=hey alfred  in .env, then restart Alfred.')
        return 0

    print("extraction did not produce the expected folder")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
