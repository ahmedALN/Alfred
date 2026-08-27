from __future__ import annotations

import asyncio

from src.ai.gemini import AlfredLiveSession
from src.tools.powershell import PowerShellTool
from src.tools.registry import ToolRegistry


async def main() -> None:
    registry = ToolRegistry()
    registry.register(PowerShellTool())

    session = AlfredLiveSession(registry)

    await session.connect()

    try:
        response = await session.ask(
            "Use PowerShell to tell me the current date and time."
        )

        print(response)
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())