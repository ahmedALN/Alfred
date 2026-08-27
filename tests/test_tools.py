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