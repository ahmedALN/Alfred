from __future__ import annotations

import re

from src.brain.types import Decision, Proposal, ProposalKind, Verdict


# ====================================================================
# Guardrails
#
# Two tiers:
#   CATASTROPHIC - irreversible, machine-destroying, or malware-shaped.
#                  Always refused. Alfred tells the user to do it by hand.
#   DANGEROUS    - security-relevant, persistent, or destructive but
#                  recoverable. Never silent: the user is asked out loud
#                  first, every time.
# Everything else the user directly asks for just runs.
# ====================================================================

CATASTROPHIC_PATTERNS: list[re.Pattern[str]] = [
    # Wiping / formatting a drive or partition
    re.compile(r"format-volume\b", re.I),
    re.compile(r"\bformat(\.com)?\s+[a-z]:", re.I),
    re.compile(r"(clear|reset)-disk\b|remove-partition\b|clear-partition\b", re.I),
    re.compile(r"\bdiskpart\b", re.I),
    re.compile(r"\bcipher\s+/w", re.I),
    re.compile(r"\bsdelete\b", re.I),
    # Deleting the OS / whole user profile roots
    re.compile(
        r"remove-item\b.*(-recurse|\brd\b|\brmdir\b).*"
        r"(c:\\windows|c:\\program files|%systemroot%|\$env:systemroot|"
        r"c:\\users\b\s*$|c:\\\s*$)",
        re.I,
    ),
    re.compile(r"\b(rd|rmdir)\b.*/s.*(c:\\windows|c:\\users\b|c:\\\s*$)", re.I),
    re.compile(r"get-childitem\s+c:\\?\s+.*remove-item", re.I),
    # Boot config / MBR
    re.compile(r"\bbcdedit\b.*(/delete|/set|/deletevalue)", re.I),
    re.compile(r"bootrec\b|bootsect\b", re.I),
    # Turning off tamper protection / wholesale AV removal
    re.compile(r"set-mppreference.*-disabletamperprotection", re.I),
    re.compile(r"uninstall-windowsfeature\b.*defender", re.I),
]

DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
    # Firewall / Defender toggles
    re.compile(r"set-netfirewallprofile.*-enabled\s+(false|0|\$false)", re.I),
    re.compile(r"netsh\s+advfirewall\s+set\b.*\b(off|disable)", re.I),
    re.compile(r"set-mppreference.*-disable", re.I),
    re.compile(r"add-mppreference.*-exclusionpath", re.I),
    # Services
    re.compile(r"(stop|disable|remove|set)-service\b", re.I),
    re.compile(r"\bsc(\.exe)?\s+(stop|delete|config|start)\b", re.I),
    re.compile(r"new-service\b", re.I),
    # Accounts / groups / credentials
    re.compile(r"(new|set|remove)-localuser\b", re.I),
    re.compile(r"\bnet\s+user\b.*(/add|/delete)", re.I),
    re.compile(r"(add|remove)-localgroupmember\b", re.I),
    re.compile(r"\bnet\s+localgroup\b.*(/add|/delete)", re.I),
    # Persistence
    re.compile(r"register-scheduledtask\b|schtasks(\.exe)?\s+/create", re.I),
    re.compile(r"\bpnputil\b|\bbcdedit\b", re.I),
    re.compile(r"reg(\.exe)?\s+add\b.*hklm", re.I),
    re.compile(r"(new|set)-itemproperty\b.*hklm", re.I),
    re.compile(r"set-executionpolicy\b", re.I),
    # Power state
    re.compile(r"(stop|restart)-computer\b|\bshutdown(\.exe)?\s+/[rs]", re.I),
    # Remote code execution / fetch-and-run
    re.compile(r"invoke-expression\b|\|\s*iex\b|\biex\s*\(", re.I),
    re.compile(r"(invoke-webrequest|iwr|curl|wget)\b.*-outfile", re.I),
    re.compile(r"downloadstring\b|downloadfile\b|start-bitstransfer\b", re.I),
    # Bulk deletion (recoverable-ish, but ask)
    re.compile(r"remove-item\b.*-recurse", re.I),
    re.compile(r"\b(rd|rmdir)\b.*/s", re.I),
    re.compile(r"\bdel\b.*/[sq]", re.I),
    re.compile(r"clear-recyclebin\b|clear-content\b", re.I),
]

# Read-only cmdlets the brain may run unattended (whole pipeline must
# consist only of these).
READONLY_CMDLETS: set[str] = {
    "get-process", "get-service", "get-item", "get-itemproperty",
    "get-childitem", "get-content", "get-date", "get-computerinfo",
    "get-ciminstance", "get-wmiobject", "get-nettcpconnection",
    "get-netudpendpoint", "get-netfirewallrule", "get-netfirewallprofile",
    "get-netfirewallportfilter", "get-netipaddress", "get-netadapter",
    "get-netroute", "get-dnsclientcache", "get-hotfix", "get-eventlog",
    "get-winevent", "get-volume", "get-disk", "get-partition",
    "get-psdrive", "get-localuser", "get-localgroup", "test-path",
    "test-connection", "test-netconnection", "resolve-dnsname",
    "measure-object", "select-object", "where-object", "sort-object",
    "format-table", "format-list", "out-string", "convertto-json",
    "select-string", "group-object",
}

# Tools that only read state.
READONLY_TOOLS: set[str] = {
    "system_info", "network_info", "computer_screenshot", "recall",
}

# Tools whose effects are easy to undo.
REVERSIBLE_TOOLS: set[str] = {
    "open_app", "remember", "desktop_control", "type_text", "mouse_click",
} | READONLY_TOOLS

# Backwards-compatible aliases (older imports / tests).
FORBIDDEN_PATTERNS = CATASTROPHIC_PATTERNS


def classify_command(command: str) -> str:
    """Return 'catastrophic', 'dangerous', or 'ordinary' for a shell command."""

    for pattern in CATASTROPHIC_PATTERNS:
        if pattern.search(command):
            return "catastrophic"

    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(command):
            return "dangerous"

    return "ordinary"


def _pipeline_is_readonly(command: str) -> bool:
    if re.search(r"[;&`{}]|\$\(|>|<|\bfunction\b|=", command):
        return False

    segments = [seg.strip() for seg in command.split("|") if seg.strip()]

    if not segments:
        return False

    for segment in segments:
        match = re.match(r"([a-z]+-[a-z]+)", segment, re.I)

        if not match or match.group(1).lower() not in READONLY_CMDLETS:
            return False

    return True


class Policy:
    """
    Classifies a tool call as AUTO / CONFIRM / FORBID.

    ``surface`` decides how permissive we are:
      "voice" - the user asked for this directly. Run it, unless it is
                dangerous (ask first) or catastrophic (refuse).
      "brain" - Alfred acted on its own. Stricter: anything that changes
                state is confirmed, mutating PowerShell always asks.
    """

    def __init__(
        self,
        autonomy: str,
        known_tools: set[str],
        surface: str = "brain",
    ) -> None:
        if autonomy not in ("ask", "auto_reversible", "full"):
            autonomy = "auto_reversible"

        self._autonomy = autonomy
        self._known_tools = known_tools
        self._surface = surface if surface in ("voice", "brain") else "brain"

    # ----------------------------------------------------------------

    def evaluate(self, proposal: Proposal) -> Decision:
        if proposal.kind is ProposalKind.SPEAK:
            return Decision(proposal, Verdict.AUTO, "speech is safe")

        tool = proposal.tool or ""

        if self._known_tools and tool not in self._known_tools:
            return Decision(proposal, Verdict.FORBID, f"unknown tool {tool!r}")

        if tool == "powershell":
            return self._evaluate_powershell(proposal)

        return self._evaluate_tool(proposal, tool)

    # ----------------------------------------------------------------

    def _evaluate_tool(self, proposal: Proposal, tool: str) -> Decision:
        if tool in READONLY_TOOLS:
            return Decision(proposal, Verdict.AUTO, "read-only tool")

        reversible = (
            proposal.reversible
            if proposal.reversible is not None
            else tool in REVERSIBLE_TOOLS
        )

        if self._surface == "voice":
            # The user asked. Reversible/ordinary tools just run.
            if reversible or self._autonomy == "full":
                return Decision(proposal, Verdict.AUTO, "user-requested action")
            return Decision(proposal, Verdict.CONFIRM, "autonomy < full")

        # brain surface
        if self._autonomy == "ask":
            return Decision(proposal, Verdict.CONFIRM, "autonomy=ask")

        if reversible:
            return Decision(proposal, Verdict.AUTO, "reversible tool")

        return Decision(proposal, Verdict.CONFIRM, "not known to be reversible")

    def _evaluate_powershell(self, proposal: Proposal) -> Decision:
        command = str(proposal.args.get("command", "")).strip()

        if not command:
            return Decision(proposal, Verdict.FORBID, "empty command")

        tier = classify_command(command)

        if tier == "catastrophic":
            return Decision(
                proposal,
                Verdict.FORBID,
                "irreversible / machine-destroying command",
            )

        if tier == "dangerous":
            return Decision(
                proposal,
                Verdict.CONFIRM,
                "security-relevant or destructive command",
            )

        if _pipeline_is_readonly(command):
            return Decision(proposal, Verdict.AUTO, "read-only PowerShell")

        if self._surface == "voice":
            return Decision(proposal, Verdict.AUTO, "user-requested command")

        # brain surface: mutating PowerShell always asks.
        return Decision(
            proposal, Verdict.CONFIRM, "PowerShell command changes state"
        )
