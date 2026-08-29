from src.tools.powershell import PowerShellTool
from src.tools.registry import ToolRegistry


def test_powershell_tool() -> None:
    tool = PowerShellTool()

    result = tool.execute(
        {
            "command": (
                'Write-Output "ALFRED_TOOL_TEST"'
            )
        }
    )

    assert result["success"] is True
    assert "ALFRED_TOOL_TEST" in result["stdout"]


def test_registry() -> None:
    registry = ToolRegistry()

    registry.register(
        PowerShellTool()
    )

    result = registry.execute(
        "powershell",
        {
            "command": (
                'Write-Output "REGISTRY_TEST"'
            )
        },
    )

    assert result["success"] is True
    assert "REGISTRY_TEST" in result["stdout"]


def test_tool_declaration() -> None:
    tool = PowerShellTool()

    declaration = tool.gemini_declaration()

    assert declaration["name"] == "powershell"
    assert "description" in declaration
    assert declaration["parameters"]["type"] == "object"
    assert "command" in declaration["parameters"]["properties"]


def test_registry_generates_gemini_declarations() -> None:
    registry = ToolRegistry()

    registry.register(
        PowerShellTool()
    )

    declarations = registry.gemini_declarations()

    assert len(declarations) == 1
    assert declarations[0]["name"] == "powershell"

def test_open_app_accepts_name_as_an_alias_for_app():
    """Models reliably reach for 'name'; a rejected call costs a whole step."""
    class FakeLauncher:
        def __init__(self):
            self.opened = []

        def open(self, app_name, target="alfred"):
            self.opened.append(app_name)

            class R:
                @staticmethod
                def as_dict():
                    return {"status": "success", "app": app_name}
            return R()

        def close(self):
            pass

    from src.tools.open_app import OpenAppTool

    for key in ("app", "name", "application", "app_name"):
        launcher = FakeLauncher()
        tool = OpenAppTool(launcher)
        out = tool.execute({key: "Notepad"})
        assert out["status"] == "success", f"{key} should be accepted"
        assert launcher.opened == ["Notepad"]

    # still rejects a genuinely missing app
    assert OpenAppTool(FakeLauncher()).execute({})["status"] == "error"
