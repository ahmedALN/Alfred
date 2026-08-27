from __future__ import annotations

import asyncio
import queue
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
    ) -> None:
        settings = load_settings()

        self.client = genai.Client(
            api_key=settings.gemini_api_key
        )

        self.model = settings.gemini_live_model
        self.registry = registry

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
    # Gemini configuration
    # ================================================================

    def _tool_declarations(
        self,
    ) -> list[dict[str, Any]]:
        return self.registry.gemini_declarations()

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

            system_instruction=(
                "You are Alfred, a concise Windows desktop "
                "AI assistant. "
                "Respond in English unless the user explicitly "
                "speaks another language. "
                "Keep responses direct and concise. "
                "When an action is successfully completed and "
                "no further information is needed, say 'Done.'. "
                "Use tools whenever the user asks you to perform "
                "an action that requires them. "

                "Alfred has an isolated child Windows session "
                "separate from the user's normal desktop. "
                "The computer_screenshot tool captures and "
                "analyzes the complete child-session display. "
                "Treat its analysis as the visual state of the "
                "child desktop at the time of capture. "
                "Do not assume the child desktop is empty unless "
                "the screenshot analysis explicitly indicates that."
            ),
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

        self._speaker_queue.put_nowait(
            audio_data
        )

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

        except Exception:
            self._connection = None

            self._stop_microphone()
            self._stop_audio_output()

            raise

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

                    print(
                        "[Turn complete]"
                    )

                    transcript_parts.clear()

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

            print(
                f"\n[Tool] {call.name}"
            )

            print(
                f"[Arguments] {arguments}"
            )

            result = await asyncio.to_thread(
                self.registry.execute,
                call.name,
                arguments,
            )

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

    async def run_forever(self) -> None:
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
        with self._state_lock:
            self._running = False

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
