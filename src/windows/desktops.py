from __future__ import annotations

from dataclasses import dataclass

from pyvda import VirtualDesktop, get_virtual_desktops


@dataclass(frozen=True)
class DesktopInfo:
    """Information about a Windows virtual desktop."""

    number: int


class DesktopManager:
    """Manage Alfred's Windows virtual desktops."""

    def list_desktops(self) -> list[DesktopInfo]:
        desktops = get_virtual_desktops()

        return [
            DesktopInfo(number=index + 1)
            for index, _ in enumerate(desktops)
        ]

    def count(self) -> int:
        return len(get_virtual_desktops())

    def current_number(self) -> int:
        return VirtualDesktop.current().number

    def switch_to(self, number: int) -> None:
        desktop_count = self.count()

        if number < 1 or number > desktop_count:
            raise ValueError(
                f"Desktop {number} does not exist. "
                f"Available desktops: 1-{desktop_count}."
            )

        VirtualDesktop(number).go()
