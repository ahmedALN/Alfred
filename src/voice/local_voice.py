from __future__ import annotations

import asyncio
import io
import json
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.brain.types import Proposal, ProposalKind, Verdict

_ROOT = Path(__file__).resolve().parent.parent.parent
_PIPER_DIR = _ROOT / "models" / "piper"
_PIPER_VOICE = _PIPER_DIR / "en_US-lessac-medium.onnx"
_PIPER_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/"
    "lessac/medium/en_US-lessac-medium"
)

_RATE = 16_000
_STOP_PHRASES = (
    "switch back", "use gemini", "back to the cloud", "cloud voice",
    "go back online",
)

_SYSTEM = """You are Alfred, running offline (the cloud voice is rate limited). \
You control this Windows PC. Keep spoken answers to one or two sentences.

If the question is about THIS computer (disk, memory, CPU, processes, \
network, firewall, ports) you must use a tool - do not guess or tell the \
user to check Settings. To use a tool, reply with ONLY a JSON object, e.g.:
{"tool": "system_info", "args": {"query": "disks"}}
For anything else, just answer in plain text.

Tools:
__TOOLS__
"""


def ensure_piper_voice() -> bool:
    if _PIPER_VOICE.exists():
        return True
    _PIPER_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import urllib.request

        for ext in (".onnx", ".onnx.json"):
            urllib.request.urlretrieve(
                _PIPER_URL + ext + "?download=true",
                _PIPER_DIR / ("en_US-lessac-medium" + ext),
            )
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[LocalVoice] could not fetch Piper voice: {exc}")
        return False


class LocalVoiceSession:
    """
    Offline voice loop: energy VAD -> faster-whisper -> local chat model
    (+ tools) -> Piper TTS. Used as a fallback while the Gemini quota is
    exhausted. Half-duplex. Best-effort - if a component is missing it
    just returns and the caller retries Gemini.
    """

    def __init__(
        self,
        chat: Any,
        registry: Any,
        policy: Any,
        *,
        stt_model: str = "base.en",
        get_listening: Callable[[], bool] | None = None,
        speak_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._chat = chat
        self._registry = registry
        self._policy = policy
        self._stt_model_name = stt_model
        self._get_listening = get_listening or (lambda: True)
        self._speak_hook = speak_hook

        self._whisper = None
        self._piper = None

    # ----------------------------------------------------------------

    def _load(self) -> bool:
        try:
            import sounddevice  # noqa: F401
            from faster_whisper import WhisperModel
            from piper import PiperVoice
        except Exception as exc:  # noqa: BLE001
            print(f"[LocalVoice] unavailable: {exc}")
            return False

        if not ensure_piper_voice():
            return False

        try:
            self._whisper = WhisperModel(
                self._stt_model_name, device="cpu", compute_type="int8"
            )
            self._piper = PiperVoice.load(str(_PIPER_VOICE))
        except Exception as exc:  # noqa: BLE001
            print(f"[LocalVoice] model load failed: {exc}")
            return False

        return True

    # ----------------------------------------------------------------

    def speak(self, text: str) -> None:
        text = text.strip()
        if not text or self._piper is None:
            return

        if self._speak_hook:
            self._speak_hook(text)

        try:
            import numpy as np
            import sounddevice as sd

            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                self._piper.synthesize_wav(text, wf)
            buf.seek(0)
            with wave.open(buf, "rb") as wf:
                rate = wf.getframerate()
                pcm = np.frombuffer(
                    wf.readframes(wf.getnframes()), dtype=np.int16
                )
            from src.voice.speakers import chosen_output

            sd.play(pcm, rate, device=chosen_output(samplerate=rate))
            sd.wait()
        except Exception as exc:  # noqa: BLE001
            print(f"[LocalVoice] playback failed: {exc}")

    def _listen(self, max_seconds: float = 10.0) -> Any | None:
        import numpy as np
        import sounddevice as sd

        chunk = 1600  # 100 ms
        start_rms, end_rms = 500.0, 300.0
        silence_needed = 8  # 0.8 s
        collected: list[Any] = []
        speaking = False
        silent = 0
        deadline = time.monotonic() + max_seconds

        try:
            with sd.RawInputStream(
                samplerate=_RATE, blocksize=chunk, dtype="int16", channels=1
            ) as stream:
                while time.monotonic() < deadline:
                    data, _ = stream.read(chunk)
                    samples = np.frombuffer(bytes(data), dtype=np.int16)
                    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))

                    if not speaking:
                        if rms > start_rms:
                            speaking = True
                            collected.append(samples)
                    else:
                        collected.append(samples)
                        if rms < end_rms:
                            silent += 1
                            if silent >= silence_needed:
                                break
                        else:
                            silent = 0
        except Exception as exc:  # noqa: BLE001
            print(f"[LocalVoice] mic error: {exc}")
            return None

        if not speaking or len(collected) < 3:
            return None
        return np.concatenate(collected)

    def _transcribe(self, audio: Any) -> str:
        try:
            import numpy as np

            segments, _ = self._whisper.transcribe(
                audio.astype(np.float32) / 32768.0, language="en"
            )
            return " ".join(s.text for s in segments).strip()
        except Exception as exc:  # noqa: BLE001
            print(f"[LocalVoice] transcription failed: {exc}")
            return ""

    def _respond(self, user_text: str) -> str:
        catalogue = "\n".join(
            f"- {t.get('name')}: {t.get('description', '')}"
            for t in self._registry.gemini_declarations()
        )
        system = _SYSTEM.replace("__TOOLS__", catalogue)
        history = [f"User: {user_text}"]

        for _ in range(3):
            try:
                raw = self._chat.generate(
                    system + "\n\n" + "\n".join(history) + "\nAlfred:",
                    temperature=0.3,
                )
            except Exception as exc:  # noqa: BLE001
                return f"Sorry, my local model had trouble: {exc}"

            call = _extract_tool_call(raw)
            if call is None:
                return raw.strip()

            name, args = call
            decision = self._policy.evaluate(
                Proposal(kind=ProposalKind.ACT, message=f"run {name}",
                         tool=name, args=args)
            )
            if decision.verdict is not Verdict.AUTO:
                history.append(
                    f"[tool {name} needs confirmation - skipped in offline mode]"
                )
                continue
            try:
                result = self._registry.execute(name, args)
            except Exception as exc:  # noqa: BLE001
                result = {"status": "error", "error": str(exc)}
            history.append(f"[tool {name} -> {json.dumps(result, default=str)[:300]}]")

        return "I looked into it but couldn't wrap it up offline."

    # ----------------------------------------------------------------

    async def _say(self, text: str) -> None:
        # Piper synth + sd.wait() block for seconds - keep them off the loop.
        await asyncio.to_thread(self.speak, text)

    async def run(self, deadline: float) -> None:
        if not self._load():
            return

        await self._say(
            "The cloud voice is rate limited, so I've switched to offline "
            "mode for a few minutes."
        )

        while time.monotonic() < deadline:
            if not self._get_listening():
                await asyncio.sleep(0.5)
                continue

            audio = await asyncio.to_thread(self._listen)
            if audio is None:
                continue

            text = await asyncio.to_thread(self._transcribe, audio)
            if not text or len(text) < 3:
                continue

            print(f"\nYou (offline): {text}")

            if any(p in text.lower() for p in _STOP_PHRASES):
                await self._say("Okay, trying the cloud voice again.")
                return

            reply = await asyncio.to_thread(self._respond, text)
            print(f"Alfred (offline): {reply}")
            await self._say(reply)


def _extract_tool_call(raw: str) -> tuple[str, dict] | None:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[-1] if "\n" in text else text
        if text.lower().startswith("json"):
            text = text[4:]
    text = text.strip()
    if not text.startswith("{"):
        s, e = text.find("{"), text.rfind("}")
        if s == -1 or e <= s:
            return None
        text = text[s : e + 1]
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or "tool" not in obj:
        return None
    return str(obj["tool"]), obj.get("args") if isinstance(obj.get("args"), dict) else {}
