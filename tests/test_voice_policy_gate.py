import os

os.environ.setdefault("GEMINI_API_KEY", "dummy-key-for-tests")

from src.ai.gemini import AlfredLiveSession  # noqa: E402
from src.brain.policy import Policy  # noqa: E402
from src.tools.registry import ToolRegistry  # noqa: E402
from src.tools.system_info import SystemInfoTool  # noqa: E402


def _session():
    registry = ToolRegistry()
    registry.register(SystemInfoTool())
    known = {"system_info", "open_app", "powershell", "desktop_control"}
    policy = Policy("full", known, surface="voice")
    return AlfredLiveSession(registry, policy=policy)


def test_ordinary_call_passes_gate():
    session = _session()
    assert session._gate_tool_call("open_app", {"app": "notepad"}, False) is None


def test_readonly_powershell_passes_gate():
    session = _session()
    gate = session._gate_tool_call(
        "powershell", {"command": "Get-Process | Select-Object -First 3"}, False
    )
    assert gate is None


def test_dangerous_call_asks_for_confirmation():
    session = _session()
    gate = session._gate_tool_call(
        "powershell", {"command": "Stop-Service -Name Spooler"}, False
    )
    assert gate is not None
    assert gate["status"] == "needs_confirmation"


def test_dangerous_call_proceeds_once_confirmed():
    session = _session()
    gate = session._gate_tool_call(
        "powershell", {"command": "Stop-Service -Name Spooler"}, True
    )
    assert gate is None


def test_catastrophic_call_is_refused_even_if_confirmed():
    session = _session()
    gate = session._gate_tool_call(
        "powershell", {"command": "Format-Volume -DriveLetter D -Force"}, True
    )
    assert gate is not None
    assert gate["status"] == "refused"


def test_no_policy_means_no_gate():
    registry = ToolRegistry()
    registry.register(SystemInfoTool())
    session = AlfredLiveSession(registry)
    assert session._gate_tool_call(
        "powershell", {"command": "Format-Volume -DriveLetter D"}, False
    ) is None
