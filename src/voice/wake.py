from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

_SAMPLE_RATE = 16_000
_CHUNK = 1280  # 80 ms, what openWakeWord expects


class WakeListener:
    """
    Always-on "Hey Alfred" detector.

    Runs openWakeWord on its own microphone stream (separate from the
    voice session's stream - Windows shared-mode audio allows this), on
    a daemon thread. Fires ``on_detect`` once per utterance, debounced.

    Loads a custom model from ``model_path`` if given/exists, otherwise
    falls back to the bundled ``hey_jarvis`` model. If openWakeWord or a
    mic isn't available it degrades to "no wake word" instead of raising.
    """

    def __init__(
        self,
        on_detect: Callable[[float], None],
        *,
        model_path: str | None = None,
        threshold: float = 0.5,
        debounce_seconds: float = 2.5,
    ) -> None:
        self._on_detect = on_detect
        self._model_path = model_path or None
        self._threshold = threshold
        self._debounce = debounce_seconds

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._last_fire = 0.0
        self.label = "hey_jarvis"

    # ----------------------------------------------------------------

    def start(self) -> bool:
        try:
            import numpy  # noqa: F401
            import sounddevice  # noqa: F401
            from openwakeword.model import Model  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            print(f"[Wake] disabled (missing dependency): {exc}")
            return False

        self._thread = threading.Thread(
            target=self._run, name="alfred-wake", daemon=True
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

    def _load_model(self):
        import openwakeword
        from openwakeword.model import Model

        path = self._model_path
        if path and Path(path).exists():
            self.label = Path(path).stem
            return Model(wakeword_models=[path], inference_framework="onnx")

        # Bundled fallback.
        try:
            openwakeword.utils.download_models(["hey_jarvis"])
        except Exception:  # noqa: BLE001
            pass
        self.label = "hey_jarvis"
        return Model(
            wakeword_models=["hey_jarvis"], inference_framework="onnx"
        )

    def _run(self) -> None:
        import numpy as np
        import sounddevice as sd

        try:
            model = self._load_model()
        except Exception as exc:  # noqa: BLE001
            print(f"[Wake] model load failed: {exc}")
            return

        print(f"[Wake] listening for '{self.label}'.")

        try:
            with sd.RawInputStream(
                samplerate=_SAMPLE_RATE,
                blocksize=_CHUNK,
                dtype="int16",
                channels=1,
            ) as stream:
                while not self._stop.is_set():
                    data, _ = stream.read(_CHUNK)

                    if self._paused.is_set():
                        continue

                    samples = np.frombuffer(bytes(data), dtype=np.int16)
                    scores = model.predict(samples)
                    score = max(scores.values()) if scores else 0.0

                    if score < self._threshold:
                        continue

                    now = time.monotonic()
                    if now - self._last_fire < self._debounce:
                        continue

                    self._last_fire = now
                    try:
                        model.reset()
                    except Exception:  # noqa: BLE001
                        pass

                    print(f"[Wake] detected ({score:.2f})")
                    try:
                        self._on_detect(score)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[Wake] on_detect failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"[Wake] audio stream error: {exc}")
