from src.tools.powershell import PowerShellTool
from src.tools.registry import ToolRegistry


def test_powershell_tool() -> None:
    tool = PowerShellTool()

    result = tool.execute(
        {"command": 'Write-Output "ALFRED_TOOL_TEST"'}
    )

    assert result["success"] is True
    assert "ALFRED_TOOL_TEST" in result["stdout"]


def test_registry() -> None:
    registry = ToolRegistry()
    tool = PowerShellTool()

    registry.register(tool)

    result = registry.execute(
        "powershell",
        {"command": 'Write-Output "REGISTRY_TEST"'},
    )

    assert result["success"] is True
    assert "REGISTRY_TEST" in result["stdout"]