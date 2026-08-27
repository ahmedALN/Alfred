from __future__ import annotations

import asyncio

from src.ai.gemini import AlfredLiveSession
from src.tools.computer_screenshot import ComputerScreenshotTool
from src.tools.open_app import OpenAppTool
from src.tools.powershell import PowerShellTool
from src.tools.registry import ToolRegistry
from src.windows.child_session import ChildSessionClient


async def main() -> None:
    registry = ToolRegistry()

    powershell_tool = PowerShellTool()
    open_app_tool = OpenAppTool()

    child_session_client = ChildSessionClient()

    screenshot_tool = ComputerScreenshotTool(
        child_session_client
    )

    registry.register(
        powershell_tool
    )

    registry.register(
        open_app_tool
    )

    registry.register(
        screenshot_tool
    )

    session = AlfredLiveSession(
        registry
    )

    try:
        await session.connect()

        print(
            "Alfred is listening."
        )

        print(
            "Speak naturally. "
            "Press Ctrl+C to exit."
        )

        await session.run_forever()

    except KeyboardInterrupt:
        print(
            "`nShutting down Alfred..."
        )

    finally:
        await session.close()

        child_session_client.close()

        open_app_tool.close()


if __name__ == "__main__":
    asyncio.run(main())
