from __future__ import annotations

import asyncio
import threading
from typing import Any

import sounddevice as sd
from google import genai
from google.genai import types

from src.config import load_settings
from src.tools.registry import ToolRegistry


class AlfredLiveSession:
    """
    Persistent Gemini Live session for Alfred.

    Audio flow:

        Microphone
            ↓
        16 kHz PCM
            ↓
        Gemini Live
            ↓
        Server-side VAD + model
            ↓
        Tool calls / response
            ↓
        24 kHz PCM
            ↓
        Speakers
    """

    INPUT_SAMPLE_RATE = 16_000
    OUTPUT_SAMPLE_RATE = 24_000

    INPUT_CHANNELS = 1
    OUTPUT_CHANNELS = 1

    # 100 ms at 16 kHz.
    INPUT_BLOCKSIZE = 1_600

    def __init__(self, registry: ToolRegistry) -> None:
        settings = load_settings()

        self.client = genai.Client(
            api_key=settings.gemini_api_key
        )

        self.model = settings.gemini_live_model
        self.registry = registry

        self.session: Any = None
        self._connection: Any = None

        self._audio_input: sd.RawInputStream | None = None
        self._audio_output: sd.RawOutputStream | None = None

        self._mic_queue: asyncio.Queue[bytes] | None = None
        self._mic_loop: asyncio.AbstractEventLoop | None = None

        self._running = False

        # Protects state touched by the PortAudio callback thread.
        self._state_lock = threading.Lock()

    # ================================================================
    # Gemini configuration
    # ================================================================

    def _tool_declarations(self) -> list[dict[str, Any]]:
        """Build Gemini function declarations."""

        declarations: list[dict[str, Any]] = []

        for tool in self.registry.list():
            if tool.name == "powershell":
                declarations.append(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "command": {
                                    "type": "string",
                                    "description": (
                                        "PowerShell command to execute "
                                        "on the Windows computer."
                                    ),
                                },
                                "timeout": {
                                    "type": "number",
                                    "description": (
                                        "Maximum execution time "
                                        "in seconds."
                                    ),
                                },
                            },
                            "required": ["command"],
                        },
                    }
                )

        return declarations

    def _config(self) -> types.LiveConnectConfig:
        """Build Gemini Live configuration."""

        declarations = self._tool_declarations()

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],

            input_audio_transcription=(
                types.AudioTranscriptionConfig()
            ),

            output_audio_transcription=(
                types.AudioTranscriptionConfig()
            ),

            # Let Gemini's server-side VAD determine speech
            # start/end boundaries.
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
                    function_declarations=declarations
                )
            ],

            thinking_config=types.ThinkingConfig(
                thinking_level="minimal"
            ),

            system_instruction=(
                "You are Alfred, a concise Windows desktop AI assistant. "
                "Respond in English unless the user explicitly speaks "
                "another language. Keep responses direct and concise. "
                "When an action is successfully completed and no further "
                "information is needed, say 'Done.'. "
                "Use tools when the user asks you to perform an action "
                "that requires them."
            ),
        )

    # ================================================================
    # Speaker
    # ================================================================

    def _start_audio_output(self) -> None:
        """Open the default Windows output device."""

        if self._audio_output is not None:
            return

        self._audio_output = sd.RawOutputStream(
            samplerate=self.OUTPUT_SAMPLE_RATE,
            channels=self.OUTPUT_CHANNELS,
            dtype="int16",
        )

        self._audio_output.start()

    def _stop_audio_output(self) -> None:
        """Close the Windows output device."""

        if self._audio_output is None:
            return

        try:
            self._audio_output.stop()
        finally:
            self._audio_output.close()
            self._audio_output = None

    def _play_audio(self, audio_data: bytes) -> None:
        """Play one raw PCM chunk from Gemini."""

        if not audio_data:
            return

        if self._audio_output is None:
            raise RuntimeError(
                "Audio output is not initialized."
            )

        self._audio_output.write(audio_data)

    # ================================================================
    # Microphone
    # ================================================================

    def _microphone_callback(
        self,
        indata: Any,
        frames: int,
        time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        """
        PortAudio microphone callback.

        Keep this callback lightweight. It only copies PCM into
        the asyncio queue.
        """

        del frames, time_info

        if status:
            print(f"[Microphone] {status}")

        with self._state_lock:
            running = self._running

        if not running:
            return

        if self._mic_queue is None:
            return

        if self._mic_loop is None:
            return

        audio_bytes = bytes(indata)

        self._mic_loop.call_soon_threadsafe(
            self._mic_queue.put_nowait,
            audio_bytes,
        )

    def _start_microphone(self) -> None:
        """Open the default Windows microphone."""

        if self._audio_input is not None:
            return

        self._mic_loop = asyncio.get_running_loop()

        self._mic_queue = asyncio.Queue(
            maxsize=20
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
        """Close the microphone."""

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
        """Open the persistent Gemini Live connection."""

        if self._connection is not None:
            raise RuntimeError(
                "Alfred Live session is already connected."
            )

        self._mic_loop = asyncio.get_running_loop()

        self._start_audio_output()

        self._connection = self.client.aio.live.connect(
            model=self.model,
            config=self._config(),
        )

        try:
            self.session = await self._connection.__aenter__()

            self._start_microphone()

            with self._state_lock:
                self._running = True

        except Exception:
            self._connection = None
            self._stop_microphone()
            self._stop_audio_output()
            raise

    # ================================================================
    # Continuous microphone stream
    # ================================================================

    async def _stream_microphone(self) -> None:
        """
        Continuously send microphone PCM to Gemini Live.

        Gemini's automatic VAD handles turn boundaries.
        """

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

            await self.session.send_realtime_input(
                audio=types.Blob(
                    data=chunk,
                    mime_type="audio/pcm;rate=16000",
                )
            )

    # ================================================================
    # Receive Gemini events
    # ================================================================

    async def _receive(self) -> None:
        """
        Continuously receive Gemini events.

        Audio playback and tool handling happen here while microphone
        streaming happens independently.
        """

        if self.session is None:
            raise RuntimeError(
                "Alfred Live session is not connected."
            )

        # Buffer Alfred's transcription fragments so they are printed
        # as one complete response instead of many tiny lines.
        transcript_parts: list[str] = []

        while True:
            async for response in self.session.receive():

                # ----------------------------------------------------
                # Tool calls
                # ----------------------------------------------------

                if response.tool_call:
                    await self._handle_tool_call(
                        response.tool_call
                    )
                    continue

                server_content = response.server_content

                if server_content is None:
                    continue

                # ----------------------------------------------------
                # Model output
                # ----------------------------------------------------

                model_turn = server_content.model_turn

                if model_turn is not None:
                    for part in model_turn.parts:

                        # Stream Gemini's audio directly to speakers.
                        if part.inline_data:
                            audio_data = part.inline_data.data

                            if isinstance(audio_data, bytes):
                                self._play_audio(audio_data)

                        # Some model responses may include text parts.
                        if part.text:
                            transcript_parts.append(
                                part.text
                            )

                # ----------------------------------------------------
                # User transcription
                # ----------------------------------------------------

                input_transcription = (
                    server_content.input_transcription
                )

                if input_transcription is not None:
                    if input_transcription.text:
                        print(
                            f"\nYou: "
                            f"{input_transcription.text}"
                        )

                # ----------------------------------------------------
                # Alfred transcription
                # ----------------------------------------------------

                output_transcription = (
                    server_content.output_transcription
                )

                if output_transcription is not None:
                    if output_transcription.text:
                        transcript_parts.append(
                            output_transcription.text
                        )

                # ----------------------------------------------------
                # Interruption
                # ----------------------------------------------------

                if server_content.interrupted:
                    print("\n[Alfred interrupted]")

                    # Throw away any unfinished transcript from the
                    # response that was interrupted.
                    transcript_parts.clear()

                # ----------------------------------------------------
                # Turn complete
                # ----------------------------------------------------

                if server_content.turn_complete:
                    response_text = "".join(
                        transcript_parts
                    ).strip()

                    if response_text:
                        print(
                            f"\nAlfred: "
                            f"{response_text}"
                        )

                    print("[Turn complete]")

                    transcript_parts.clear()

            await asyncio.sleep(0)

    # ================================================================
    # Tool handling
    # ================================================================

    async def _handle_tool_call(
        self,
        tool_call: Any,
    ) -> None:
        """Execute Gemini tool calls and return their results."""

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

            arguments = dict(call.args or {})

            # Useful during development: show exactly what Alfred
            # decided to execute.
            print(
                f"\n[Tool] {call.name}"
            )

            print(
                f"[Arguments] {arguments}"
            )

            result = self.registry.execute(
                call.name,
                arguments,
            )

            print(
                f"[Tool Result] {result}"
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
    # Run loop
    # ================================================================

    async def run_forever(self) -> None:
        """
        Keep Alfred alive.

        Two concurrent pipelines operate independently:

        microphone → Gemini
        Gemini → audio / tools
        """

        if self.session is None:
            raise RuntimeError(
                "Alfred Live session is not connected."
            )

        microphone_task = asyncio.create_task(
            self._stream_microphone()
        )

        receive_task = asyncio.create_task(
            self._receive()
        )

        try:
            done, pending = await asyncio.wait(
                {
                    microphone_task,
                    receive_task,
                },
                return_when=asyncio.FIRST_EXCEPTION,
            )

            for task in done:
                exception = task.exception()

                if exception is not None:
                    raise exception

            for task in pending:
                task.cancel()

            await asyncio.gather(
                *pending,
                return_exceptions=True,
            )

        finally:
            microphone_task.cancel()
            receive_task.cancel()

            await asyncio.gather(
                microphone_task,
                receive_task,
                return_exceptions=True,
            )

    # ================================================================
    # Cleanup
    # ================================================================

    async def close(self) -> None:
        """Close Gemini Live and all audio resources."""

        with self._state_lock:
            self._running = False

        try:
            if self._mic_queue is not None:
                await self._mic_queue.put(b"")

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