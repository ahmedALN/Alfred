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
    """Persistent Gemini Live session for Alfred."""

    INPUT_SAMPLE_RATE = 16_000
    OUTPUT_SAMPLE_RATE = 24_000

    INPUT_CHANNELS = 1
    OUTPUT_CHANNELS = 1

    INPUT_BLOCKSIZE = 3_200  # 200 ms at 16 kHz

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

        # A queue belongs to exactly one speech turn.
        # We never reuse an old turn's queue.
        self._active_mic_queue: asyncio.Queue[bytes] | None = None
        self._mic_loop: asyncio.AbstractEventLoop | None = None

        self._recording = False
        self._recording_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Gemini configuration
    # ------------------------------------------------------------------

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
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),

            # We control speech-turn boundaries ourselves for this
            # push-to-talk test.
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=(
                    types.AutomaticActivityDetection(
                        disabled=True
                    )
                )
            ),

            tools=[
                types.Tool(
                    function_declarations=declarations,
                )
            ],

            thinking_config=types.ThinkingConfig(
                thinking_level="minimal",
            ),

            system_instruction=(
                "You are Alfred, a concise Windows desktop AI assistant. "
                "Respond in English unless the user explicitly speaks "
                "another language. Keep responses direct and concise. "
                "When an action is successfully completed and no further "
                "information is needed, say 'Done.'"
            ),
        )

    # ------------------------------------------------------------------
    # Audio output
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Audio input
    # ------------------------------------------------------------------

    def _microphone_callback(
        self,
        indata: Any,
        frames: int,
        time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        """Receive microphone audio from sounddevice."""

        del frames, time_info

        if status:
            print(f"Microphone status: {status}")

        with self._recording_lock:
            recording = self._recording
            queue = self._active_mic_queue

        # Critical:
        # If we are not actively recording a turn, do absolutely
        # nothing with the captured audio.
        if not recording or queue is None:
            return

        if self._mic_loop is None:
            return

        audio_bytes = bytes(indata)

        # Put audio into THIS turn's queue.
        # A later turn gets a brand-new queue, preventing stale audio
        # from leaking across activity boundaries.
        self._mic_loop.call_soon_threadsafe(
            queue.put_nowait,
            audio_bytes,
        )

    def _start_microphone(self) -> None:
        """Open the default Windows microphone."""

        if self._audio_input is not None:
            return

        self._mic_loop = asyncio.get_running_loop()

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

    async def _capture_turn_audio(self) -> None:
        """
        Send exactly one manually bounded speech turn.

        No audio is sent before activity_start or after activity_end.
        """

        if self.session is None:
            raise RuntimeError(
                "Alfred Live session is not connected."
            )

        queue: asyncio.Queue[bytes] = asyncio.Queue()

        # Publish this queue as the active queue before recording starts.
        with self._recording_lock:
            self._active_mic_queue = queue
            self._recording = True

        try:
            # Start the activity BEFORE any audio is sent.
            await self.session.send_realtime_input(
                activity_start=types.ActivityStart()
            )

            print("Listening... Press Enter to stop.")

            await asyncio.to_thread(input)

        finally:
            # Stop accepting microphone audio FIRST.
            with self._recording_lock:
                self._recording = False
                self._active_mic_queue = None

        # Tell the sender that capture has ended.
        await queue.put(b"")

        # Drain exactly the audio belonging to this turn.
        while True:
            chunk = await queue.get()

            if chunk == b"":
                break

            await self.session.send_realtime_input(
                audio=types.Blob(
                    data=chunk,
                    mime_type="audio/pcm;rate=16000",
                )
            )

        # No more audio will be sent after this point.
        await self.session.send_realtime_input(
            activity_end=types.ActivityEnd()
        )

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the persistent Gemini Live connection."""

        if self._connection is not None:
            raise RuntimeError(
                "Alfred Live session is already connected."
            )

        self._mic_loop = asyncio.get_running_loop()

        self._start_audio_output()
        self._start_microphone()

        self._connection = self.client.aio.live.connect(
            model=self.model,
            config=self._config(),
        )

        try:
            self.session = await self._connection.__aenter__()
        except Exception:
            self._connection = None
            self._stop_microphone()
            self._stop_audio_output()
            raise

    # ------------------------------------------------------------------
    # Push-to-talk
    # ------------------------------------------------------------------

    async def push_to_talk(self) -> str:
        """
        Record one speech turn.

        Enter = start
        Enter = stop
        """

        if self.session is None:
            raise RuntimeError(
                "Alfred Live session is not connected."
            )

        print()
        print("Press Enter to start talking...")

        await asyncio.to_thread(input)

        # Capture and stream this turn only.
        await self._capture_turn_audio()

        return await self._receive_until_complete()

    # ------------------------------------------------------------------
    # Receive Gemini response
    # ------------------------------------------------------------------

    async def _receive_until_complete(self) -> str:
        """Receive one Gemini turn and stream its audio."""

        transcript_parts: list[str] = []

        while True:
            async for response in self.session.receive():

                # Gemini function calling.
                if response.tool_call:
                    await self._handle_tool_call(
                        response.tool_call
                    )
                    continue

                server_content = response.server_content

                if server_content is None:
                    continue

                model_turn = server_content.model_turn

                if model_turn is not None:
                    for part in model_turn.parts:

                        # Gemini Live audio output.
                        if part.inline_data:
                            audio_data = part.inline_data.data

                            if isinstance(audio_data, bytes):
                                self._play_audio(audio_data)

                        # Text, if present.
                        if part.text:
                            transcript_parts.append(
                                part.text
                            )

                # Gemini's transcription of Alfred's spoken output.
                output_transcription = (
                    server_content.output_transcription
                )

                if output_transcription is not None:
                    if output_transcription.text:
                        transcript_parts.append(
                            output_transcription.text
                        )

                # Gemini's transcription of your microphone input.
                input_transcription = (
                    server_content.input_transcription
                )

                if input_transcription is not None:
                    if input_transcription.text:
                        print(
                            f"\nYou: "
                            f"{input_transcription.text}"
                        )

                if server_content.turn_complete:
                    return "".join(
                        transcript_parts
                    ).strip()

            await asyncio.sleep(0)

    # ------------------------------------------------------------------
    # Tool handling
    # ------------------------------------------------------------------

    async def _handle_tool_call(
        self,
        tool_call: Any,
    ) -> None:
        """Execute Gemini tool calls and return their results."""

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

            result = self.registry.execute(
                call.name,
                arguments,
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

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close Gemini Live and both audio devices."""

        with self._recording_lock:
            self._recording = False
            self._active_mic_queue = None

        try:
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

            self._active_mic_queue = None
            self._mic_loop = None