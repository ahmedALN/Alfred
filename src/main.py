from __future__ import annotations

import asyncio

from src.ai.gemini import AlfredLiveSession
from src.tools.powershell import PowerShellTool
from src.tools.registry import ToolRegistry


async def main() -> None:
    registry = ToolRegistry()
    registry.register(PowerShellTool())

    session = AlfredLiveSession(registry)

    try:
        await session.connect()

        print("Alfred is listening.")
        print("Speak naturally. Press Ctrl+C to exit.")

        await session.run_forever()

    except KeyboardInterrupt:
        print("\nShutting down Alfred...")

    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())