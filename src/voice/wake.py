from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

_SAMPLE_RATE = 16_000
_CHUNK = 1280  # 80 ms

_ROOT = Path(__file__).resolve().parent.parent.parent
_VOSK_DIR = _ROOT / "models" / "vosk-model-small-en-us-0.15"


class WakeListener:
    """
    Always-on wake detector on its own microphone stream (separate from
    the voice session's - Windows shared-mode audio allows this), on a
    daemon thread. Fires ``on_detect`` once per utterance, debounced.

    Two backends:
      - a custom phrase (``phrase``, e.g. "hey alfred") via Vosk with a
        constrained grammar - no training, ~40 MB model.
      - otherwise openWakeWord: a custom ``model_path`` .onnx, else the
        bundled "hey jarvis".

    Any failure degrades to "no wake word" rather than raising.
    """

    def __init__(
        self,
        on_detect: Callable[[float], None],
        *,
        phrase: str | None = None,
        model_path: str | None = None,
        threshold: float = 0.5,
        debounce_seconds: float = 2.5,
    ) -> None:
        self._on_detect = on_detect
        self._phrase = (phrase or "").strip().lower() or None
        self._model_path = model_path or None
        self._threshold = threshold
        self._debounce = debounce_seconds

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._last_fire = 0.0
        self.label = self._phrase or "hey_jarvis"

    # ----------------------------------------------------------------

    def start(self) -> bool:
        try:
            import numpy  # noqa: F401
            import sounddevice  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            print(f"[Wake] disabled (missing dependency): {exc}")
            return False

        target = self._run_vosk if self._phrase else self._run_oww

        self._thread = threading.Thread(
            target=target, name="alfred-wake", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    # ----------------------------------------------------------------

    def _fire(self, score: float) -> None:
        now = time.monotonic()
        if now - self._last_fire < self._debounce:
            return
        self._last_fire = now
        print(f"[Wake] '{self.label}' detected ({score:.2f})")
        try:
            self._on_detect(score)
        except Exception as exc:  # noqa: BLE001
            print(f"[Wake] on_detect failed: {exc}")

    # ---- Vosk (custom phrase) -------------------------------------

    def _run_vosk(self) -> None:
        try:
            import sounddevice as sd
            from vosk import KaldiRecognizer, Model, SetLogLevel
        except Exception as exc:  # noqa: BLE001
            print(f"[Wake] Vosk unavailable ({exc}); falling back to hey_jarvis.")
            self._phrase = None
            self._run_oww()
            return

        if not _VOSK_DIR.exists():
            print(
                "[Wake] Vosk model missing. Run: "
                "python -m src.voice.setup_wakeword"
            )
            print("[Wake] falling back to 'hey jarvis' for now.")
            self._phrase = None
            self._run_oww()
            return

        SetLogLevel(-1)
        model = Model(str(_VOSK_DIR))
        grammar = json.dumps([self._phrase, "[unk]"])
        rec = KaldiRecognizer(model, _SAMPLE_RATE, grammar)

        print(f"[Wake] listening for '{self._phrase}'.")

        try:
            with sd.RawInputStream(
                samplerate=_SAMPLE_RATE, blocksize=_CHUNK,
                dtype="int16", channels=1,
            ) as stream:
                while not self._stop.is_set():
                    data, _ = stream.read(_CHUNK)
                    if self._paused.is_set():
                        rec.Reset()
                        continue

                    raw = bytes(data)
                    final = rec.AcceptWaveform(raw)
                    text = json.loads(
                        rec.Result() if final else rec.PartialResult()
                    ).get("text" if final else "partial", "")

                    if self._phrase in text.lower():
                        rec.Reset()
                        self._fire(1.0)
        except Exception as exc:  # noqa: BLE001
            print(f"[Wake] Vosk stream error: {exc}")

    # ---- openWakeWord --------------------------------------------

    def _load_oww(self):
        import openwakeword
        from openwakeword.model import Model

        path = self._model_path
        if path and Path(path).exists():
            self.label = Path(path).stem
            return Model(wakeword_models=[path], inference_framework="onnx")

        try:
            openwakeword.utils.download_models(["hey_jarvis"])
        except Exception:  # noqa: BLE001
            pass
        self.label = "hey_jarvis"
        return Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")

    def _run_oww(self) -> None:
        import numpy as np
        import sounddevice as sd

        try:
            model = self._load_oww()
        except Exception as exc:  # noqa: BLE001
            print(f"[Wake] model load failed: {exc}")
            return

        print(f"[Wake] listening for '{self.label}'.")

        try:
            with sd.RawInputStream(
                samplerate=_SAMPLE_RATE, blocksize=_CHUNK,
                dtype="int16", channels=1,
            ) as stream:
                while not self._stop.is_set():
                    data, _ = stream.read(_CHUNK)
                    if self._paused.is_set():
                        continue

                    samples = np.frombuffer(bytes(data), dtype=np.int16)
                    scores = model.predict(samples)
                    score = max(scores.values()) if scores else 0.0

                    if score >= self._threshold:
                        try:
                            model.reset()
                        except Exception:  # noqa: BLE001
                            pass
                        self._fire(score)
        except Exception as exc:  # noqa: BLE001
            print(f"[Wake] audio stream error: {exc}")
