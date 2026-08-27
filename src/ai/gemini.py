from __future__ import annotations

import asyncio
from typing import Any

from google import genai
from google.genai import types

from src.config import load_settings
from src.tools.registry import ToolRegistry


class AlfredLiveSession:
    """Persistent Gemini Live session for Alfred."""

    def __init__(self, registry: ToolRegistry) -> None:
        settings = load_settings()

        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = settings.gemini_live_model
        self.registry = registry

        self.session: Any = None
        self._connection: Any = None

    def _tool_declarations(self) -> list[dict[str, Any]]:
        """Build Gemini function declarations from Alfred's tools."""

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
                                        "Maximum execution time in seconds."
                                    ),
                                },
                            },
                            "required": ["command"],
                        },
                    }
                )

        return declarations

    def _config(self) -> types.LiveConnectConfig:
        """Build the Gemini Live session configuration."""

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

    async def connect(self) -> None:
        """Open and enter the persistent Gemini Live connection."""

        if self._connection is not None:
            raise RuntimeError(
                "Alfred Live session is already connected."
            )

        self._connection = self.client.aio.live.connect(
            model=self.model,
            config=self._config(),
        )

        self.session = await self._connection.__aenter__()

    async def ask(self, prompt: str) -> str:
        """Send text to the Live session and return its transcript."""

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
        """Receive one model turn, handling tool calls."""

        transcript_parts: list[str] = []

        while True:
            async for response in self.session.receive():
                # Live API function calling is manual: Alfred must execute
                # the requested function and send the response back.
                if response.tool_call:
                    await self._handle_tool_call(response.tool_call)
                    continue

                server_content = response.server_content

                if server_content is None:
                    continue

                # Gemini 3.1 Live can send multiple model-turn parts in
                # a single server event, so inspect every part.
                model_turn = server_content.model_turn

                if model_turn is not None:
                    for part in model_turn.parts:
                        if part.text:
                            transcript_parts.append(part.text)

                # Output transcription contains the spoken response text.
                output_transcription = (
                    server_content.output_transcription
                )

                if output_transcription is not None:
                    if output_transcription.text:
                        transcript_parts.append(
                            output_transcription.text
                        )

                if server_content.turn_complete:
                    return "".join(transcript_parts).strip()

            await asyncio.sleep(0)

    async def _handle_tool_call(self, tool_call: Any) -> None:
        """Execute requested Alfred tools and return their results."""

        function_responses: list[types.FunctionResponse] = []

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
        """Close the persistent Live connection."""

        if self._connection is None:
            return

        try:
            await self._connection.__aexit__(
                None,
                None,
                None,
            )
        finally:
            self.session = None
            self._connection = None