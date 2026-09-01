"""The desktop bridge - only meaningful once the native helper is built.

These need DesktopBridge.exe, which is C# and is produced by
`python -m src.setup` (or `dotnet build -c Release` under
src/windows/native). Build output is not committed, so on a fresh
clone the binary is legitimately absent.

That is a reason to skip, not to fail. Somebody who has just cloned
this and run the tests should see the suite pass and be told what is
not built yet - three red failures say "this project is broken" when
what is true is "you have not run setup".
"""

import pytest

from src.windows.desktop_bridge import DesktopBridgeClient


def _built() -> bool:
    try:
        return DesktopBridgeClient()._bridge_executable().exists()
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _built(),
    reason="DesktopBridge.exe not built - run: python -m src.setup",
)


def test_bridge_ping() -> None:
    bridge = DesktopBridgeClient()

    try:
        result = bridge.ping()

        assert result["pong"] is True

    finally:
        bridge.close()


def test_bridge_reports_desktops() -> None:
    bridge = DesktopBridgeClient()

    try:
        assert bridge.count() >= 1
        assert bridge.current() >= 1

    finally:
        bridge.close()


def test_bridge_can_handle_multiple_requests() -> None:
    bridge = DesktopBridgeClient()

    try:
        first = bridge.count()
        second = bridge.count()

        assert first >= 1
        assert second == first

    finally:
        bridge.close()