from __future__ import annotations

import asyncio
import queue
import threading
import time
import uuid
from typing import Any

import sounddevice as sd
from google import genai
from google.genai import types

from src.config import load_settings
from src.memory.learner import MemoryLearner
from src.memory.store import MemoryStore
from src.tools.registry import ToolRegistry


_CONNECTION_ERROR_HINTS = (
    "aborted", "closed", "connectionclosed", "1008", "1011", "1006",
    "policy violation", "going away", "timeout", "timed out",
    "deadline", "unavailable", "reset by peer", "broken pipe",
)


def _is_connection_error(exc: BaseException) -> bool:
    """
    True for transient transport/session failures we should reconnect
    through (Gemini Live sockets drop on idle/timeout/quota), not for
    real bugs.
    """

    if isinstance(exc, (ConnectionError, OSError, asyncio.TimeoutError)):
        return True

    name = type(exc).__name__.lower()
    module = (type(exc).__module__ or "").lower()
    text = f"{exc}".lower()

    if "websockets" in module or "connectionclosed" in name:
        return True
    if "apierror" in name or "genai" in module:
        return any(h in text for h in _CONNECTION_ERROR_HINTS)

    return any(h in text for h in _CONNECTION_ERROR_HINTS)


class AlfredLiveSession:
    """
    Persistent Gemini Live session for Alfred.

    Screenshots are handled by the computer_screenshot tool itself.
    The tool sends the exact captured image to a dedicated
    multimodal Interactions API model and returns the resulting
    visual analysis as ordinary tool-response text.
    """

    INPUT_SAMPLE_RATE = 16_000
    OUTPUT_SAMPLE_RATE = 24_000

    INPUT_CHANNELS = 1
    OUTPUT_CHANNELS = 1

    INPUT_BLOCKSIZE = 1_600
    MIC_QUEUE_SIZE = 50

    def __init__(
        self,
        registry: ToolRegistry,
        store: MemoryStore | None = None,
        learner: MemoryLearner | None = None,
        brain: Any = None,
        policy: Any = None,
        activation: Any = None,
        half_duplex: bool = True,
    ) -> None:
        settings = load_settings()

        self.client = genai.Client(
            api_key=settings.gemini_api_key
        )

        self.model = settings.gemini_live_model
        self.registry = registry

        self._store = store
        self._learner = learner
        self._brain = brain
        self._policy = policy
        self._activation = activation
        self._half_duplex = half_duplex
        self._last_audio_queued_at = 0.0
        self._reconnect_backoff_base = 2.0

        self._passphrase = settings.voice_passphrase
        self._passphrase_window = settings.voice_passphrase_window
        self._passphrase_ok_until = 0.0

        self._local_voice_factory: Any = None
        self._local_voice_cooldown = settings.local_voice_cooldown
        self._session_id = uuid.uuid4().hex

        # Extra long-lived coroutines to run alongside the session
        # (e.g. the background task-queue worker).
        self._background_factories: list[Any] = []

        # Partial input-transcription fragments for the current user
        # turn, joined and handed to the brain on turn completion.
        self._input_transcript_parts: list[str] = []

        # Turn-time memory recall: facts already shown this session, and
        # a simple gap so we surface at most one memory block per window.
        self._surfaced_fact_ids: set[int] = set()
        self._last_memory_surface = 0.0

        self.session: Any = None
        self._connection: Any = None

        self._audio_input: sd.RawInputStream | None = None
        self._mic_queue: asyncio.Queue[bytes] | None = None
        self._mic_loop: asyncio.AbstractEventLoop | None = None

        self._audio_output: sd.RawOutputStream | None = None

        self._speaker_queue: queue.Queue[
            bytes | None
        ] = queue.Queue()

        self._speaker_thread: threading.Thread | None = None
        self._speaker_stop = threading.Event()

        self._running = False
        self._state_lock = threading.Lock()

    # ================================================================
    # Brain integration
    # ================================================================

    @property
    def session_key(self) -> str:
        """Stable id for the current voice session (for the audit log)."""

        return self._session_id

    def attach_brain(self, brain: Any) -> None:
        """Wire the background awareness loop in after construction."""

        self._brain = brain

    def attach_policy(self, policy: Any) -> None:
        self._policy = policy

    def attach_local_voice(self, factory: Any) -> None:
        """factory() -> a LocalVoiceSession, used while Gemini quota is out."""
        self._local_voice_factory = factory

    def add_background_task(self, factory: Any) -> None:
        """
        Register a zero-arg callable returning a coroutine to run for the
        lifetime of the session (cancelled on shutdown).
        """

        self._background_factories.append(factory)

    def notify_woken(self) -> None:
        """
        Thread-safe: called from the wake-word / hotkey listener to have
        Alfred give a brief spoken acknowledgement.
        """

        loop = self._mic_loop

        if loop is None or self.session is None:
            return

        def _schedule() -> None:
            asyncio.create_task(
                self.inject_system_prompt(
                    "(System: the user just activated you with the wake "
                    "word or hotkey. Reply with a very short "
                    "acknowledgement - 'Yes?' or 'Go ahead.' - then wait "
                    "for their request.)"
                )
            )

        try:
            loop.call_soon_threadsafe(_schedule)
        except Exception as exc:  # noqa: BLE001
            print(f"[Alfred] notify_woken failed: {exc}")

    def _gate_tool_call(
        self,
        name: str,
        arguments: dict[str, Any],
        pre_confirmed: bool,
    ) -> dict[str, Any] | None:
        """
        Run a user-requested tool call through the safety policy.

        Returns None to let the call proceed, or a dict to send back to
        the model instead of executing (refusal or confirmation prompt).
        """

        if self._policy is None:
            return None

        from src.brain.types import Proposal, ProposalKind, Verdict

        proposal = Proposal(
            kind=ProposalKind.ACT,
            message=f"run {name}",
            tool=name,
            args=arguments,
        )

        decision = self._policy.evaluate(proposal)

        if decision.verdict is Verdict.AUTO:
            return None

        if decision.verdict is Verdict.FORBID:
            return {
                "status": "refused",
                "reason": decision.reason,
                "instruction": (
                    "Do not retry. Tell the user you can't do this because "
                    "it is irreversible or destructive, and they should do "
                    "it themselves if they are certain."
                ),
            }

        # CONFIRM (a "dangerous" action). Optionally gate on a spoken
        # passphrase so someone else's voice can't drive these.
        if self._passphrase and time.monotonic() > self._passphrase_ok_until:
            return {
                "status": "needs_passphrase",
                "reason": decision.reason,
                "instruction": (
                    "This is a protected action. Tell the user it needs the "
                    "passphrase - ask them to say it, then try again. Do NOT "
                    "reveal or hint at the passphrase."
                ),
            }

        if pre_confirmed:
            return None

        return {
            "status": "needs_confirmation",
            "reason": decision.reason,
            "instruction": (
                "Briefly tell the user exactly what this will do and why it "
                "is risky, then ask them to confirm. If they clearly agree, "
                "call this same tool again with the extra argument "
                '"_confirmed": true. If they decline, drop it.'
            ),
        }

    async def _surface_relevant_memory(self, utterance: str) -> None:
        """
        After a user turn, pull up stored facts relevant to what they
        just said (beyond the always-on core already in the system
        prompt) and hand them to the model as context for the next
        exchange. Cheap, rate-limited, best-effort.
        """

        if self._learner is None or self.session is None:
            return

        if len(utterance.split()) < 4:
            return

        now = time.monotonic()

        if now - self._last_memory_surface < 20.0:
            return

        try:
            core = await asyncio.to_thread(self._learner.core_fact_ids)
            facts = await asyncio.to_thread(
                self._learner.recall, utterance, 3
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[Memory] turn-time recall failed: {exc}")
            return

        fresh = [
            fact
            for fact in facts
            if fact.id not in core and fact.id not in self._surfaced_fact_ids
        ]

        if not fresh:
            return

        self._last_memory_surface = now

        for fact in fresh:
            self._surfaced_fact_ids.add(fact.id)

        block = "; ".join(fact.content for fact in fresh)

        await self.inject_system_prompt(
            f"(System: possibly relevant memory — {block})"
        )

    async def inject_system_prompt(self, text: str) -> None:
        """
        Push a system-authored turn into the live session so Alfred
        voices it in character. Used by the brain for proactive
        messages and post-action summaries.
        """

        if self.session is None:
            return

        try:
            await self.session.send_client_content(
                turns=types.Content(
                    role="user",
                    parts=[types.Part(text=text)],
                ),
                turn_complete=True,
            )
        except Exception as exc:
            print(f"[Brain] inject_system_prompt failed: {exc}")

    # ================================================================
    # Gemini configuration
    # ================================================================

    def _tool_declarations(
        self,
    ) -> list[dict[str, Any]]:
        return self.registry.gemini_declarations()

    def _memory_context_block(self) -> str:
        if self._learner is None:
            return ""

        try:
            context = self._learner.recall_context()
        except Exception as exc:
            print(f"[Memory] failed to recall context: {exc}")
            return ""

        return f"\n\n{context}" if context else ""

    def _system_instruction(self) -> str:
        base = (
            "You are Alfred, an AI that lives on this Windows PC. You run "
            "at startup and stay resident. You have a background awareness "
            "loop, a long-term memory across sessions, a task agent for "
            "multi-step jobs, and your own virtual desktop. You know your "
            "own tools and limits - if asked what you are or can do, call "
            "what_can_you_do and answer from that, don't guess. "
            "Respond in English unless the user explicitly "
            "speaks another language. "
            "Keep responses direct and concise. "
            "When an action is successfully completed and "
            "no further information is needed, say 'Done.'. "
            "Use tools whenever the user asks you to perform "
            "an action that requires them. "

            "Alfred has an isolated desktop separate from the "
            "user's normal desktop. The computer_screenshot tool "
            "captures and analyzes its full display. The "
            "desktop_control tool sees that desktop and drives its "
            "mouse and keyboard: call desktop_control action='look' "
            "to get current pixel coordinates, then act on them, then "
            "'look' again to confirm. Working on this desktop does "
            "not disturb the user. "
            "Do not assume this desktop is empty unless a capture "
            "explicitly indicates that. "

            "For questions about the machine's resources, "
            "processes, or network/firewall state, prefer the "
            "system_info and network_info tools over raw "
            "powershell, since their output is structured and "
            "reliable. Use powershell for anything those tools "
            "don't cover. "

            "Whenever the user states a lasting preference, "
            "corrects your behavior, or you learn a stable fact "
            "about their machine or setup, call the 'remember' "
            "tool to store it for future sessions. If you are "
            "unsure whether you already know something relevant, "
            "call 'recall' before asking the user or guessing. "

            "Alfred also has a background awareness loop that watches "
            "the machine and may hand you a proactive note prefixed "
            "'(System: proactive)'. When you get one, relay it to the "
            "user briefly and naturally in your own voice, then stop. "
            "If the user tells you to stop mentioning a topic, to be "
            "quiet, or to pause the background loop, acknowledge it in "
            "one short sentence. "

            "For a job that needs several steps in sequence (organizing "
            "files, auditing and fixing settings, setting something up), "
            "call run_task with a clear goal and let the background agent "
            "do it; tell the user you've started and don't wait. Do quick "
            "single actions yourself. "

            "Run whatever the user asks without hesitation. If a tool "
            "returns status 'needs_confirmation', follow its instruction: "
            "tell the user what the action does and the risk, and only "
            "if they agree, call the same tool again with '_confirmed': "
            "true. If a tool returns 'needs_passphrase', tell the user "
            "the action is protected and ask them to say the passphrase "
            "(never say the passphrase yourself), then retry. If a tool "
            "returns status 'refused', do not retry - "
            "explain why and suggest they do it manually."
        )

        return base + self._memory_context_block()

    def _config(self) -> types.LiveConnectConfig:
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],

            input_audio_transcription=(
                types.AudioTranscriptionConfig()
            ),

            output_audio_transcription=(
                types.AudioTranscriptionConfig()
            ),

            realtime_input_config=(
                types.RealtimeInputConfig(
                    automatic_activity_detection=(
                        types.AutomaticActivityDetection(
                            disabled=False,
                        )
                    )
                )
            ),

            tools=[
                types.Tool(
                    function_declarations=(
                        self._tool_declarations()
                    )
                )
            ],

            thinking_config=types.ThinkingConfig(
                thinking_level="minimal"
            ),

            system_instruction=self._system_instruction(),
        )

    # ================================================================
    # Speaker
    # ================================================================

    def _speaker_worker(self) -> None:
        if self._audio_output is None:
            return

        while not self._speaker_stop.is_set():
            try:
                chunk = self._speaker_queue.get(
                    timeout=0.1
                )
            except queue.Empty:
                continue

            if chunk is None:
                break

            try:
                self._audio_output.write(
                    chunk
                )
            except Exception as exc:
                print(
                    f"[Speaker] playback error: {exc}"
                )

    def _start_audio_output(self) -> None:
        if self._audio_output is not None:
            return

        self._audio_output = sd.RawOutputStream(
            samplerate=self.OUTPUT_SAMPLE_RATE,
            channels=self.OUTPUT_CHANNELS,
            dtype="int16",
        )

        self._audio_output.start()

        self._speaker_stop.clear()

        self._speaker_thread = threading.Thread(
            target=self._speaker_worker,
            name="alfred-speaker",
            daemon=True,
        )

        self._speaker_thread.start()

    def _clear_speaker_queue(self) -> None:
        while True:
            try:
                self._speaker_queue.get_nowait()
            except queue.Empty:
                return

    def _queue_audio(
        self,
        audio_data: bytes,
    ) -> None:
        if not audio_data:
            return

        self._last_audio_queued_at = time.monotonic()
        self._speaker_queue.put_nowait(
            audio_data
        )

    def _alfred_is_speaking(self) -> bool:
        """True while Alfred's own audio is playing (or just finished)."""

        if not self._speaker_queue.empty():
            return True
        return (time.monotonic() - self._last_audio_queued_at) < 0.35

    def _stop_audio_output(self) -> None:
        self._speaker_stop.set()

        try:
            self._speaker_queue.put_nowait(
                None
            )
        except queue.Full:
            pass

        if self._speaker_thread is not None:
            self._speaker_thread.join(
                timeout=2.0
            )

            self._speaker_thread = None

        if self._audio_output is not None:
            try:
                self._audio_output.stop()
            finally:
                self._audio_output.close()
                self._audio_output = None

        self._clear_speaker_queue()

    # ================================================================
    # Microphone
    # ================================================================

    def _enqueue_mic_audio(
        self,
        chunk: bytes,
    ) -> None:
        if self._mic_queue is None:
            return

        try:
            self._mic_queue.put_nowait(
                chunk
            )

            return

        except asyncio.QueueFull:
            pass

        try:
            self._mic_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass

        try:
            self._mic_queue.put_nowait(
                chunk
            )
        except asyncio.QueueFull:
            pass

    def _microphone_callback(
        self,
        indata: Any,
        frames: int,
        time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        del frames, time_info

        if status:
            print(
                f"[Microphone] {status}"
            )

        with self._state_lock:
            running = self._running

        if not running:
            return

        if self._mic_loop is None:
            return

        if self._mic_queue is None:
            return

        chunk = bytes(
            indata
        )

        self._mic_loop.call_soon_threadsafe(
            self._enqueue_mic_audio,
            chunk,
        )

    def _start_microphone(self) -> None:
        if self._audio_input is not None:
            return

        self._mic_loop = (
            asyncio.get_running_loop()
        )

        self._mic_queue = asyncio.Queue(
            maxsize=self.MIC_QUEUE_SIZE
        )

        self._audio_input = sd.RawInputStream(
            samplerate=self.INPUT_SAMPLE_RATE,
            channels=self.INPUT_CHANNELS,
            dtype="int16",
            blocksize=self.INPUT_BLOCKSIZE,
            callback=self._microphone_callback,
        )

        self._audio_input.start()

    def _stop_microphone(self) -> None:
        if self._audio_input is None:
            return

        try:
            self._audio_input.stop()
        finally:
            self._audio_input.close()
            self._audio_input = None

    # ================================================================
    # Connection
    # ================================================================

    async def connect(self) -> None:
        if self._connection is not None:
            raise RuntimeError(
                "Alfred Live session is already connected."
            )

        self._mic_loop = (
            asyncio.get_running_loop()
        )

        self._start_audio_output()

        self._connection = (
            self.client.aio.live.connect(
                model=self.model,
                config=self._config(),
            )
        )

        try:
            self.session = (
                await self._connection.__aenter__()
            )

            with self._state_lock:
                self._running = True

            self._start_microphone()

            await self._send_startup_greeting()

        except Exception:
            self._connection = None

            self._stop_microphone()
            self._stop_audio_output()

            raise

    async def _send_startup_greeting(self) -> None:
        if self.session is None:
            return

        # If Alfred is wake-word gated, stay quiet until spoken to.
        if self._activation is not None and not self._activation.is_listening:
            print("[Alfred] ready - say the wake word or press the hotkey.")
            return

        try:
            await self.session.send_client_content(
                turns=types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            text=(
                                "(System: Alfred has just started up "
                                "and is now listening. Greet the user "
                                "briefly by name if you know it, "
                                "mention anything relevant you "
                                "remember from before if it's useful, "
                                "and ask what they need. Keep it to "
                                "one or two short sentences.)"
                            )
                        )
                    ],
                ),
                turn_complete=True,
            )
        except Exception as exc:
            print(f"[Startup] failed to send greeting prompt: {exc}")

    # ================================================================
    # Microphone → Gemini
    # ================================================================

    async def _stream_microphone(self) -> None:
        if self.session is None:
            raise RuntimeError(
                "Alfred Live session is not connected."
            )

        if self._mic_queue is None:
            raise RuntimeError(
                "Microphone queue is not initialized."
            )

        while True:
            chunk = await self._mic_queue.get()

            if chunk == b"":
                return

            with self._state_lock:
                running = self._running

            if not running:
                return

            # Conversation window: only stream to the model while
            # Alfred is actually listening (woken by "Hey Alfred" or
            # the hotkey). Outside that, drop the audio.
            if (
                self._activation is not None
                and not self._activation.is_listening
            ):
                continue

            # Half-duplex: don't feed Alfred's own voice (picked up by
            # the mic on a speaker setup) back into the model.
            if self._half_duplex and self._alfred_is_speaking():
                continue

            await self.session.send_realtime_input(
                audio=types.Blob(
                    data=chunk,
                    mime_type="audio/pcm;rate=16000",
                )
            )

    # ================================================================
    # Gemini → Alfred
    # ================================================================

    async def _receive(self) -> None:
        if self.session is None:
            raise RuntimeError(
                "Alfred Live session is not connected."
            )

        transcript_parts: list[str] = []

        while True:
            async for response in self.session.receive():

                if getattr(response, "usage_metadata", None) is not None:
                    try:
                        from src.usage import record_response

                        record_response(response)
                    except Exception:  # noqa: BLE001
                        pass

                if response.tool_call:
                    await self._handle_tool_call(
                        response.tool_call
                    )

                    continue

                server_content = (
                    response.server_content
                )

                if server_content is None:
                    continue

                model_turn = (
                    server_content.model_turn
                )

                if model_turn is not None:
                    for part in model_turn.parts:

                        if part.inline_data:
                            audio_data = (
                                part.inline_data.data
                            )

                            if isinstance(
                                audio_data,
                                bytes,
                            ):
                                self._queue_audio(
                                    audio_data
                                )

                        if part.text:
                            transcript_parts.append(
                                part.text
                            )

                input_transcription = (
                    server_content.input_transcription
                )

                if input_transcription is not None:
                    if input_transcription.text:
                        print(
                            f"\nYou: "
                            f"{input_transcription.text}"
                        )

                        self._input_transcript_parts.append(
                            input_transcription.text
                        )

                        if self._store is not None:
                            self._store.add_turn(
                                self._session_id,
                                "user",
                                input_transcription.text,
                            )

                output_transcription = (
                    server_content.output_transcription
                )

                if output_transcription is not None:
                    if output_transcription.text:
                        transcript_parts.append(
                            output_transcription.text
                        )

                if server_content.interrupted:
                    self._clear_speaker_queue()

                    transcript_parts.clear()

                    print(
                        "\n[Alfred interrupted]"
                    )

                if server_content.turn_complete:
                    response_text = "".join(
                        transcript_parts
                    ).strip()

                    if response_text:
                        print(
                            f"\nAlfred: "
                            f"{response_text}"
                        )

                        if self._store is not None:
                            self._store.add_turn(
                                self._session_id,
                                "alfred",
                                response_text,
                            )

                    print(
                        "[Turn complete]"
                    )

                    transcript_parts.clear()

                    user_utterance = "".join(
                        self._input_transcript_parts
                    ).strip()

                    self._input_transcript_parts.clear()

                    if user_utterance and self._activation is not None:
                        self._activation.note_activity()

                    if (
                        user_utterance
                        and self._passphrase
                        and self._passphrase in user_utterance.lower()
                    ):
                        self._passphrase_ok_until = (
                            time.monotonic() + self._passphrase_window
                        )
                        print("[Alfred] passphrase accepted.")

                    if user_utterance and self._brain is not None:
                        try:
                            await self._brain.note_user_reply(
                                user_utterance
                            )
                        except Exception as exc:
                            print(
                                f"[Brain] note_user_reply failed: {exc}"
                            )

                    if user_utterance and self._learner is not None:
                        asyncio.create_task(
                            self._surface_relevant_memory(user_utterance)
                        )

            await asyncio.sleep(0)

    # ================================================================
    # Tool handling
    # ================================================================

    async def _handle_tool_call(
        self,
        tool_call: Any,
    ) -> None:
        if self.session is None:
            raise RuntimeError(
                "Alfred Live session is not connected."
            )

        function_responses: list[
            types.FunctionResponse
        ] = []

        for call in tool_call.function_calls:

            if not call.name:
                raise RuntimeError(
                    "Gemini returned a function call "
                    "without a name."
                )

            arguments = dict(
                call.args or {}
            )

            pre_confirmed = bool(arguments.pop("_confirmed", False))

            print(
                f"\n[Tool] {call.name}"
            )

            print(
                f"[Arguments] {arguments}"
            )

            gate = self._gate_tool_call(
                call.name, arguments, pre_confirmed
            )

            if gate is not None:
                print(f"[Policy] {gate['status']}: {gate.get('reason', '')}")

                if self._store is not None:
                    self._store.add_tool_event(
                        self._session_id,
                        call.name,
                        arguments,
                        gate,
                        False,
                    )

                function_responses.append(
                    types.FunctionResponse(
                        name=call.name,
                        id=call.id,
                        response=gate,
                    )
                )
                continue

            try:
                result = await asyncio.to_thread(
                    self.registry.execute,
                    call.name,
                    arguments,
                )
            except Exception as exc:  # noqa: BLE001
                # A failing tool must never take the whole session
                # down: report it back to the model and keep going.
                result = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                print(f"[Tool Error] {call.name}: {result['error']}")

            if not isinstance(
                result,
                dict,
            ):
                result = {
                    "status": "error",
                    "error": (
                        "Tool returned a non-dictionary result."
                    ),
                }

            print(
                f"[Tool Result] {result}"
            )

            if self._store is not None:
                success = result.get("status") != "error"

                self._store.add_tool_event(
                    self._session_id,
                    call.name,
                    arguments,
                    result,
                    success,
                )

            function_responses.append(
                types.FunctionResponse(
                    name=call.name,
                    id=call.id,
                    response=result,
                )
            )

        await self.session.send_tool_response(
            function_responses=function_responses
        )

    # ================================================================
    # Runtime
    # ================================================================

    async def _reopen_session(self) -> None:
        """Drop the current Gemini Live socket and open a fresh one."""

        try:
            if self._connection is not None:
                await self._connection.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass

        self.session = None
        self._connection = None

        self._connection = self.client.aio.live.connect(
            model=self.model, config=self._config()
        )
        self.session = await self._connection.__aenter__()

    async def run_forever(self) -> None:
        if self.session is None:
            raise RuntimeError(
                "Alfred Live session is not connected."
            )

        # Background tasks start once and survive voice reconnects.
        background: set[asyncio.Task[None]] = set()

        if self._brain is not None:
            background.add(asyncio.create_task(self._brain.run()))

        if self._activation is not None and not self._activation.always_on:
            background.add(asyncio.create_task(self._activation.run()))

        for factory in self._background_factories:
            background.add(asyncio.create_task(factory()))

        backoff = self._reconnect_backoff_base
        consecutive_failures = 0

        try:
            while True:
                with self._state_lock:
                    if not self._running:
                        break

                voice = {
                    asyncio.create_task(self._stream_microphone()),
                    asyncio.create_task(self._receive()),
                }

                done, _ = await asyncio.wait(
                    voice | background,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                # A background task ending (it shouldn't) is not
                # recoverable here.
                for task in done:
                    if task in background:
                        bg_exc = task.exception()
                        if bg_exc is not None:
                            raise bg_exc
                        raise RuntimeError(
                            "A background task exited unexpectedly."
                        )

                for task in voice:
                    task.cancel()
                await asyncio.gather(*voice, return_exceptions=True)

                exc = next(
                    (t.exception() for t in done
                     if t in voice and t.exception() is not None),
                    None,
                )

                if exc is None:
                    break  # a voice task ended cleanly -> shut down

                if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                    raise exc

                if not _is_connection_error(exc):
                    raise exc

                text = f"{exc}".lower()
                is_quota = "exhausted" in text or "429" in text or "quota" in text

                try:
                    from src.usage import USAGE

                    USAGE.record_error("quota" if is_quota else "disconnect")
                except Exception:  # noqa: BLE001
                    pass

                # Quota is out - talk locally for a while instead of
                # hammering reconnects.
                if is_quota and self._local_voice_factory is not None:
                    print(
                        "[Alfred] Gemini quota exhausted - switching to "
                        f"offline voice for {self._local_voice_cooldown:.0f}s."
                    )
                    try:
                        local = self._local_voice_factory()
                        await local.run(
                            time.monotonic() + self._local_voice_cooldown
                        )
                    except Exception as lv_exc:  # noqa: BLE001
                        print(f"[Alfred] offline voice failed: {lv_exc}")
                    try:
                        await self._reopen_session()
                        backoff = self._reconnect_backoff_base
                        consecutive_failures = 0
                        print("[Alfred] back on the cloud voice.")
                    except Exception as re:  # noqa: BLE001
                        print(f"[Alfred] reconnect after offline failed: {re}")
                    continue

                consecutive_failures += 1
                if consecutive_failures > 10:
                    print(
                        "[Alfred] giving up after repeated reconnect "
                        f"failures: {exc}"
                    )
                    raise exc

                print(
                    f"[Alfred] voice connection dropped ({type(exc).__name__}: "
                    f"{exc}); reconnecting in {backoff:.0f}s"
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

                try:
                    await self._reopen_session()
                    backoff = self._reconnect_backoff_base
                    consecutive_failures = 0
                    print("[Alfred] reconnected.")
                except Exception as reconnect_exc:  # noqa: BLE001
                    print(f"[Alfred] reconnect failed: {reconnect_exc}")

        finally:
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)

    # ================================================================
    # Cleanup
    # ================================================================

    async def _distill_session(self) -> None:
        if self._store is None or self._learner is None:
            return

        transcript = self._store.session_turns(self._session_id)

        if not transcript:
            return

        try:
            learned = await asyncio.to_thread(
                self._learner.distill_session,
                transcript,
            )

            if learned:
                print(
                    f"[Memory] learned {learned} new fact(s) "
                    "from this session."
                )

            merged = await asyncio.to_thread(self._learner.dedupe)
            if merged:
                print(f"[Memory] merged {merged} duplicate fact(s).")

        except Exception as exc:
            print(f"[Memory] session distillation failed: {exc}")

    async def close(self) -> None:
        with self._state_lock:
            self._running = False

        await self._distill_session()

        try:
            if self._mic_queue is not None:
                try:
                    self._mic_queue.put_nowait(
                        b""
                    )
                except asyncio.QueueFull:
                    pass

            if self._connection is not None:
                await self._connection.__aexit__(
                    None,
                    None,
                    None,
                )

        finally:
            self.session = None
            self._connection = None

            self._stop_microphone()
            self._stop_audio_output()

            self._mic_queue = None
            self._mic_loop = None
