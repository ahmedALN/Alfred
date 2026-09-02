from __future__ import annotations

import re

from src.brain.shellsafe import normalise
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
    # Deleting the OS / whole user profile roots.
    #
    # Deliberately order-independent. This was one ordered pattern that
    # wanted the switch before the path, and `-Path` is positional - so
    # `Remove-Item C:\Windows -Recurse -Force`, which is how anybody
    # would actually type it, scored merely "dangerous" while
    # `Remove-Item -Recurse C:\Windows` scored catastrophic. The same
    # command, twice, with two different verdicts.
    re.compile(
        r"(?is)^(?=.*remove-item\b)(?=.*(?:-recurse|\brd\b|\brmdir\b))"
        r"(?=.*(?:c:\\windows|c:\\program files|%systemroot%|"
        r"\$env:systemroot|\$env:windir|c:\\users\s*[\\\"']?\s*$|"
        r"c:\\users\\?\s*(?:$|[-\"'])|c:\\\s*$))",
    ),
    re.compile(r"\b(rd|rmdir)\b.*/s.*(c:\\windows|c:\\users\b|c:\\\s*$)", re.I),
    re.compile(r"get-childitem\s+c:\\?\s+.*remove-item", re.I),
    # Boot config / MBR
    re.compile(r"\bbcdedit\b.*(/delete|/set|/deletevalue)", re.I),
    re.compile(r"bootrec\b|bootsect\b", re.I),
    # Turning off tamper protection / wholesale AV removal
    re.compile(r"set-mppreference.*-disabletamperprotection", re.I),
    re.compile(r"uninstall-windowsfeature\b.*defender", re.I),
    # Destroying the ways back. Deleting shadow copies or backups is
    # not a thing an assistant ever has a reason to do, and it is the
    # first move of every piece of ransomware that has ever run on
    # Windows.
    re.compile(r"\bvssadmin\b.*\bdelete\b.*\bshadow", re.I),
    re.compile(r"\bwbadmin\b.*\bdelete\b", re.I),
    re.compile(r"get-wmiobject\b.*win32_shadowcopy.*\bdelete\b", re.I),
    re.compile(r"remove-computerrestorepoint\b|disable-computerrestore\b", re.I),
    re.compile(r"\bbcdedit\b.*recoveryenabled\s+no", re.I),
    # Emptying a user's whole profile through the .NET API rather than
    # a cmdlet - Remove-Item's patterns above never see this spelling.
    re.compile(
        r"\[(?:system\.)?io\.directory\]::delete\s*\(\s*[\"']?"
        r"(?:c:\\windows|c:\\program files|c:\\users\\?[\"']|"
        r"\$env:userprofile[\"']?\s*\))",
        re.I,
    ),
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
    # Bulk file moves / copies / renames (can clobber, ask first)
    re.compile(r"(move|rename)-item\b.*-recurse", re.I),
    re.compile(
        r"get-childitem\b.*-recurse.*\|\s*(move|remove|copy|rename)-item",
        re.I,
    ),
    re.compile(r"\brobocopy\b.*/(mov|move|mir|purge)", re.I),
    re.compile(r"\b(move|xcopy)\b.*/[esy]", re.I),
    # ---------------------------------------------------------------
    # Added after probing the gate. Each of these came back "ordinary"
    # and each one does the thing the gate exists to stop.
    # ---------------------------------------------------------------
    # A wildcard is a recursion you did not have to type.
    # `Remove-Item -Path C:\Users\me\Documents\* -Force` empties a
    # folder as thoroughly as -Recurse does.
    re.compile(r"remove-item\b[^|;\n]*[\\/]\*", re.I),
    re.compile(r"remove-item\b[^|;\n]*\*\.\w+", re.I),
    # Base64 hides the command from every pattern above. shellsafe
    # decodes it and the decoded text is judged too, but a command
    # that arrives encoded at all is not one to run without asking.
    re.compile(r"-e(?:c|nc?o?d?e?d?c?o?m?m?a?n?d?)\s+[A-Za-z0-9+/=]{16,}", re.I),
    re.compile(r"frombase64string\b", re.I),
    # Deleting or overwriting through .NET, which no cmdlet pattern
    # sees.
    re.compile(r"\[(?:system\.)?io\.(?:file|directory)\]::(delete|move)", re.I),
    re.compile(r"\[(?:system\.)?io\.file\]::writeall(text|bytes|lines)", re.I),
    # Persistence by writing the profile that every future shell runs.
    re.compile(r"(set|add|out|clear)-(content|file|item)\b[^|;\n]*\$profile", re.I),
    re.compile(r"\$profile[^|;\n]*(-value|>>|>)", re.I),
    re.compile(
        r"hkcu:?\\+software\\+microsoft\\+windows\\+currentversion\\+run",
        re.I,
    ),
    re.compile(r"[\\/]start\s*menu[\\/]programs[\\/]startup", re.I),
    # Sending something out. The gate cared a lot about what came in
    # and nothing about what left.
    re.compile(
        r"(invoke-restmethod|invoke-webrequest)\b[^|;\n]*"
        r"-method\s+(post|put|patch)",
        re.I,
    ),
    re.compile(r"(invoke-restmethod|invoke-webrequest)\b[^|;\n]*-(body|infile)\b", re.I),
    re.compile(r"\buploadfile\b|\buploadstring\b", re.I),
    re.compile(r"send-mailmessage\b", re.I),
    # Taking ownership / rewriting permissions on somebody else's files.
    re.compile(r"\btakeown\b|\bicacls\b[^|;\n]*/(grant|deny|setowner|reset)", re.I),
    re.compile(r"set-acl\b", re.I),
    # Creating a process the long way round.
    re.compile(r"\bwmic\b[^|;\n]*process[^|;\n]*call[^|;\n]*create", re.I),
    re.compile(r"invoke-cimmethod\b[^|;\n]*win32_process", re.I),
    re.compile(r"\bnew-scheduledtask\b|\bset-scheduledtask\b", re.I),
    # Reaching another machine, or letting one reach this one.
    re.compile(r"\bnet\s+use\b", re.I),
    re.compile(r"enable-psremoting\b|new-pssession\b|invoke-command\b.*-computername", re.I),
    # Add-Type with a P/Invoke signature is native code, compiled here,
    # to do something no cmdlet would.
    re.compile(r"add-type\b[^\n]*dllimport", re.I),
    re.compile(r"reflection\.assembly\]::load", re.I),
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
    """Return 'catastrophic', 'dangerous', or 'ordinary' for a shell command.

    Judged on what the command says once PowerShell has read it -
    aliases expanded, backticks gone, split strings rejoined, any
    -EncodedCommand payload decoded, any wrapped shell unwrapped - as
    well as on the literal text. The worse of the two verdicts wins, so
    normalising can only ever raise the tier, never lower it.
    """

    if not command:
        return "ordinary"

    texts = [command]

    flattened = normalise(command)

    if flattened and flattened != command:
        texts.append(flattened)

    for pattern in CATASTROPHIC_PATTERNS:
        if any(pattern.search(text) for text in texts):
            return "catastrophic"

    for pattern in DANGEROUS_PATTERNS:
        if any(pattern.search(text) for text in texts):
            return "dangerous"

    return "ordinary"


# ====================================================================
# The same thing, done with the mouse
#
# Asked to delete everything on the Desktop, Alfred had three
# Remove-Item commands refused by the gate above - and then did this:
#
#   ui_control {"action":"key","window":"Desktop - File Explorer",
#               "keys":"Ctrl+A"}                              -> ok
#   ui_control {"action":"key","window":"Desktop - File Explorer",
#               "keys":"Delete"}                              -> ok
#
# Both allowed, because the gate reads shell commands and a keystroke
# is not one. Select-all-and-Delete in a file window is a bulk delete
# whatever it is typed into, and the files survived that run by luck of
# focus rather than by anything stopping it.
#
# So the gestures are classified too. Deliberately narrow: this is
# about deleting and emptying, not about clicking in general, because a
# gate that asks before every click is a gate that gets turned off.
# ====================================================================

# Windows that hold files, where a Delete key means those files.
_FILE_WINDOW = re.compile(
    r"explorer|desktop|this pc|documents|downloads|pictures|videos|"
    r"music|recycle bin|onedrive|\bfiles?\b|folder",
    re.I,
)

_DELETE_KEY = re.compile(
    r"\{?(?:del|delete)\}?$|\{?(?:del|delete)\}?\+|shift.{0,3}\+?.{0,3}del",
    re.I,
)

# Controls and menu items that remove things.
_DESTRUCTIVE_CONTROL = re.compile(
    r"^\s*(?:delete|delete permanently|permanently delete|remove|"
    r"move to (?:the )?(?:recycle )?bin|empty (?:the )?recycle bin|"
    r"delete all|clear all|remove all|delete folder|delete file|"
    r"uninstall|reset|erase|format)\b",
    re.I,
)


def classify_gesture(tool: str, args: dict) -> str:
    """'dangerous' or 'ordinary' for a UI action, by what it destroys."""

    if tool not in ("ui_control", "desktop_control"):
        return "ordinary"

    action = str(args.get("action") or "").strip().lower()
    window = str(args.get("window") or args.get("app") or "")

    if action == "key":
        keys = str(args.get("keys") or "")

        if not _DELETE_KEY.search(keys):
            return "ordinary"

        # Where it lands is what it means. Delete in a file window is
        # a bulk delete of whatever is selected; Delete in Notepad is
        # the key next to Backspace, and asking about that would make
        # the gate something to be switched off.
        #
        # An unnamed window is the raw-input case - desktop_control
        # types into whatever has focus, and we do not know what that
        # is. Unknown gets the careful answer.
        if not window or _FILE_WINDOW.search(window):
            return "dangerous"

        return "ordinary"

    if action in ("click", "double_click", "invoke", "select", "open_item"):
        target = str(args.get("name") or args.get("item") or "")

        if _DESTRUCTIVE_CONTROL.match(target):
            return "dangerous"

        return "ordinary"

    if action == "menu":
        path = str(args.get("path") or "")

        if any(
            _DESTRUCTIVE_CONTROL.match(part.strip())
            for part in re.split(r"->|>|\|", path)
        ):
            return "dangerous"

    return "ordinary"


def _pipeline_is_readonly(command: str) -> bool:
    # Judged on the expanded text, so `gci | sls` is recognised as the
    # read it is and `gci | ri` is recognised as the write it is.
    # A command that grew extra lines under normalisation was carrying
    # something - an encoded payload, a wrapped shell - and is never
    # waved through unattended whatever the payload turned out to say.
    flattened = normalise(command)

    if "\n" in flattened.strip():
        return False

    command = flattened or command

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

        # A gesture can do what a command was refused for.
        gesture = classify_gesture(tool, proposal.args or {})

        if gesture == "dangerous":
            return Decision(
                proposal,
                Verdict.CONFIRM,
                "this deletes things through the window rather than the shell",
            )

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
