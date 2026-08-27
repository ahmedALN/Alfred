from src.windows.desktops import DesktopManager


def test_desktop_manager_can_read_desktops() -> None:
    manager = DesktopManager()

    assert manager.count() >= 1
    assert manager.current_number() >= 1