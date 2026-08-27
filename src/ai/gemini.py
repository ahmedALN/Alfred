from __future__ import annotations

import asyncio
from typing import Any

import sounddevice as sd
from google import genai
from google.genai import types

from src.config import load_settings
from src.tools.registry import ToolRegistry


class AlfredLiveSession:
    """Persistent Gemini Live session for Alfred."""

    OUTPUT_SAMPLE_RATE = 24000
    OUTPUT_CHANNELS = 1

    def __init__(self, registry: ToolRegistry) -> None:
        settings = load_settings()

        self.client = genai.Client(
            api_key=settings.gemini_api_key
        )

        self.model = settings.gemini_live_model
        self.registry = registry

        self.session: Any = None
        self._connection: Any = None
        self._audio_stream: sd.RawOutputStream | None = None

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
            output_audio_transcription=types.AudioTranscriptionConfig(),
            tools=[
                types.Tool(
                    function_declarations=declarations,
                )
            ],
            thinking_config=types.ThinkingConfig(
                thinking_level="minimal",
            ),
        )

    def _start_audio_output(self) -> None:
        """Open the default Windows output device."""

        if self._audio_stream is not None:
            return

        self._audio_stream = sd.RawOutputStream(
            samplerate=self.OUTPUT_SAMPLE_RATE,
            channels=self.OUTPUT_CHANNELS,
            dtype="int16",
        )

        self._audio_stream.start()

    def _stop_audio_output(self) -> None:
        """Close the Windows audio output device."""

        if self._audio_stream is None:
            return

        try:
            self._audio_stream.stop()
        finally:
            self._audio_stream.close()
            self._audio_stream = None

    def _play_audio(self, audio_data: bytes) -> None:
        """Write a PCM chunk to the output device."""

        if not audio_data:
            return

        if self._audio_stream is None:
            raise RuntimeError("Audio output is not initialized.")

        self._audio_stream.write(audio_data)

    async def connect(self) -> None:
        """Open and enter the persistent Gemini Live connection."""

        if self._connection is not None:
            raise RuntimeError(
                "Alfred Live session is already connected."
            )

        self._start_audio_output()

        self._connection = self.client.aio.live.connect(
            model=self.model,
            config=self._config(),
        )

        try:
            self.session = await self._connection.__aenter__()
        except Exception:
            self._connection = None
            self._stop_audio_output()
            raise

    async def ask(self, prompt: str) -> str:
        """Send a text prompt and stream the spoken response."""

        if self.session is None:
            raise RuntimeError(
                "Alfred Live session is not connected."
            )

        if not prompt.strip():
            raise ValueError("Prompt cannot be empty.")

        await self.session.send_realtime_input(
            text=prompt,
        )

        return await self._receive_until_complete()

    async def _receive_until_complete(self) -> str:
        """Receive one model turn and stream its audio."""

        transcript_parts: list[str] = []

        while True:
            async for response in self.session.receive():
                # Handle tool calls before normal model output.
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
                        # Gemini Live audio output arrives as raw PCM.
                        if part.inline_data:
                            audio_data = part.inline_data.data

                            if isinstance(audio_data, bytes):
                                self._play_audio(audio_data)

                        # Some responses can also contain text.
                        if part.text:
                            transcript_parts.append(part.text)

                output_transcription = (
                    server_content.output_transcription
                )

                if output_transcription is not None:
                    if output_transcription.text:
                        transcript_parts.append(
                            output_transcription.text
                        )

                if server_content.turn_complete:
                    return "".join(
                        transcript_parts
                    ).strip()

            await asyncio.sleep(0)

    async def _handle_tool_call(
        self,
        tool_call: Any,
    ) -> None:
        """Execute requested tools and return their results."""

        function_responses: list[
            types.FunctionResponse
        ] = []

        for call in tool_call.function_calls:
            if not call.name:
                raise RuntimeError(
                    "Gemini returned a function call without a name."
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

    async def close(self) -> None:
        """Close the Live connection and audio device."""

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
            self._stop_audio_output()