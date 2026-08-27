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

        print("Alfred is connected.")
        print("Push-to-talk mode.")

        while True:
            try:
                response = await session.push_to_talk()

                if response:
                    print(f"\nAlfred: {response}")
                else:
                    print("\nAlfred returned no transcript.")

            except KeyboardInterrupt:
                break

    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())