"""
python -m src.knowledge  -  Alfred's built-in Windows playbook.

    seed        load the playbook into long-term memory (idempotent)
    list        show the playbook entries
    clear       remove playbook entries from memory

These are curated "how to do X well on Windows" facts. Once seeded they
are retrieved by relevance whenever Alfred plans or runs a task, so the
planner and executor start from good practice instead of guessing. Real
successes still distil into skills on top of this.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_DB = Path(__file__).resolve().parent.parent / os.getenv(
    "ALFRED_MEMORY_DB", "alfred_memory.sqlite3"
)

_SOURCE = "playbook"

# Each entry: (category, content). category is one of the memory
# categories; "correction" is used for "do it this way, not that way".
WINDOWS_PLAYBOOK: list[tuple[str, str]] = [
    # --- tool choice -------------------------------------------------
    ("correction", "To drive a normal Windows app (Spotify, a browser, "
     "Explorer, Settings, Office) use the ui_control tool - call "
     "action='tree' once to see the real controls, then click/type by "
     "ref or name. Only fall back to desktop_control (screenshots) when "
     "ui_control 'tree' returns nothing useful."),
    ("correction", "For files, processes, services, network and firewall "
     "state, prefer the system_info and network_info tools, then "
     "powershell. Their output is structured; raw console text is not."),
    ("system", "open_app resolves an app by natural name via Get-StartApps "
     "(covers Store + Start-menu apps), so 'spotify', 'settings', "
     "'task manager', 'control panel' all work. If it returns "
     "status='not_found' the app genuinely isn't installed - don't retry "
     "the same name."),

    # --- app recipes ----------------------------------------------
    ("system", "Spotify: open it, ui_control tree, type the artist/song "
     "into the 'Search' Edit control, then click the 'Play' button (or "
     "the first result). Confirm with ui_control get on the now-playing "
     "text. Ctrl+L also focuses the Spotify search box."),
    ("system", "A web browser: ui_control tree, type the URL or query into "
     "the address bar (often named 'Address and search bar'), press "
     "Enter with ui_control key '{ENTER}'."),
    ("system", "File Explorer: press Ctrl+L or click the address bar to "
     "type a path directly, then Enter. Ctrl+A selects all items in the "
     "current folder."),
    ("system", "To change a Windows setting, open 'settings' and use "
     "ui_control, or use the matching PowerShell cmdlet - many Settings "
     "pages have one (e.g. Set-NetFirewallProfile, Set-MpPreference)."),

    # --- powershell idioms --------------------------------------
    ("system", "List files with Get-ChildItem; add -Filter '*.pdf' for a "
     "type, -Recurse to descend, and pipe to Measure-Object for a count "
     "or (Measure-Object -Property Length -Sum) for total size."),
    ("system", "Make a folder: New-Item -ItemType Directory -Force -Path "
     "'<path>'  (the -Force means it won't error if it already exists)."),
    ("system", "Move files: Move-Item -Path '<src>' -Destination '<dst>'. "
     "Always quote paths. Create the destination folder first. Use "
     "-Force to overwrite."),
    ("system", "Delete: Remove-Item -Path '<path>'. For a folder tree add "
     "-Recurse -Force. This is permanent - it does NOT go to the Recycle "
     "Bin unless you use the shell."),
    ("system", "Check disk space: Get-Volume, or system_info query='disks'. "
     "Free space is the FreeGB / SizeRemaining field."),
    ("system", "Listening ports: Get-NetTCPConnection -State Listen | "
     "Select LocalAddress,LocalPort,OwningProcess. netstat also works but "
     "is unstructured."),
    ("system", "Firewall status: Get-NetFirewallProfile | Select "
     "Name,Enabled. Re-enable a profile: Set-NetFirewallProfile -Profile "
     "Domain,Public,Private -Enabled True."),
    ("system", "Startup apps: Get-CimInstance Win32_StartupCommand, or "
     "Get-StartApps. Registry Run keys are HKCU:\\Software\\Microsoft\\"
     "Windows\\CurrentVersion\\Run and the HKLM equivalent; there is also "
     "the Startup folder and Task Scheduler."),
    ("system", "Top CPU/RAM processes: Get-Process | Sort-Object CPU "
     "-Descending | Select -First 10, or system_info query='top_processes'."),
    ("system", "Kill a process by name: Stop-Process -Name <name> -Force. "
     "By id: Stop-Process -Id <pid>."),
    ("system", "User folders: $env:USERPROFILE is the home dir. Downloads, "
     "Documents, Desktop, Pictures are directly under it. Never hard-code "
     "'C:\\Users\\<name>' - read $env:USERPROFILE."),
    ("system", "Read a text file: Get-Content '<path>'. Append a line: "
     "Add-Content '<path>' '<text>'. Overwrite: Set-Content."),
    ("system", "Zip: Compress-Archive -Path '<items>' -DestinationPath "
     "'<file>.zip'. Unzip: Expand-Archive -Path '<file>.zip' -"
     "DestinationPath '<folder>'."),

    # --- verification habits -----------------------------------
    ("correction", "After doing something, verify it: list the folder "
     "again, read back the control's value, re-query the state. A tool "
     "returning success only means the call ran, not that the goal is "
     "met."),
    ("correction", "When a step needs data from an earlier step (a file "
     "name, a count, a path), read it from the earlier tool result in the "
     "history rather than guessing."),

    # --- safety --------------------------------------------------
    ("correction", "Never disable the firewall, Windows Defender, or "
     "tamper protection; never format a drive, delete C:\\Windows or a "
     "whole user profile, or run downloaded scripts. Alfred refuses "
     "these outright - tell the user to do it themselves."),
    ("correction", "Stopping or disabling a service, changing scheduled "
     "tasks, editing HKLM, bulk-deleting or bulk-moving files, and "
     "shutting down or restarting the PC all need the user's explicit "
     "OK first, even when they asked for the task."),

    # --- networking & diagnostics -------------------------------
    ("system", "IP config: Get-NetIPConfiguration, or Get-NetIPAddress "
     "-AddressFamily IPv4. Public IP needs a web call - "
     "(Invoke-RestMethod ifconfig.me/ip)."),
    ("system", "Wi-Fi: netsh wlan show interfaces (SSID, signal, channel); "
     "netsh wlan show profiles lists saved networks; Get-NetAdapter shows "
     "all adapters and their status."),
    ("system", "Test connectivity: Test-NetConnection <host> -Port <n> "
     "(TCP + latency + route). Test-Connection <host> is ping. "
     "Resolve-DnsName <host> for DNS."),
    ("system", "Flush DNS: Clear-DnsClientCache. Renew DHCP: "
     "ipconfig /release then ipconfig /renew (both need admin)."),
    ("system", "What's using a port: Get-NetTCPConnection -LocalPort <n> | "
     "ForEach-Object { Get-Process -Id $_.OwningProcess }."),
    ("system", "Windows/build version: Get-ComputerInfo | Select "
     "WindowsProductName, OsVersion, WindowsVersion; or [System."
     "Environment]::OSVersion; or 'winver' opens the dialog."),
    ("system", "Installed apps: Get-Package, or Get-ItemProperty "
     "HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\* | "
     "Select DisplayName, DisplayVersion. winget list also works."),
    ("system", "Battery / power: Get-CimInstance Win32_Battery; "
     "powercfg /batteryreport writes an HTML report; powercfg /list shows "
     "power plans."),
    ("system", "Uptime: (Get-Date) - (Get-CimInstance Win32_OperatingSystem)"
     ".LastBootUpTime, or system_info query='overview'."),

    # --- troubleshooting ----------------------------------------
    ("system", "App is frozen: Get-Process <name> to find it, then "
     "Stop-Process -Name <name> -Force (ask the user first). Restart it "
     "with open_app."),
    ("system", "Disk nearly full: check big folders with Get-ChildItem "
     "<path> -Recurse -File | Sort Length -Descending | Select -First 20. "
     "The usual culprits are Downloads, %TEMP%, and old Windows.old."),
    ("system", "Clear temp files: Remove-Item $env:TEMP\\* -Recurse -Force "
     "-ErrorAction SilentlyContinue (safe; skips locked files). This "
     "needs the user's OK as a bulk delete."),
    ("system", "High CPU/RAM: system_info query='top_processes' or "
     "Get-Process | Sort WS -Descending | Select -First 10 Name, "
     "@{N='RAM_MB';E={[int]($_.WS/1MB)}}, CPU."),
    ("system", "Recent errors: Get-WinEvent -FilterHashtable "
     "@{LogName='System'; Level=2; StartTime=(Get-Date).AddHours(-24)} | "
     "Select -First 20 TimeCreated, Id, Message. Level 1=Critical, "
     "2=Error, 3=Warning. FilterHashtable Level takes ONE integer - for "
     "several levels pipe to Where-Object { $_.Level -in 1,2 } instead."),

    # --- window / desktop management --------------------------
    ("system", "Alfred's own apps live on virtual desktop 2 so they don't "
     "cover the user's work. open_app puts them there; desktop_control "
     "borrows focus there for ~100ms then returns it."),
    ("system", "To bring a window forward: ui_control focus_window "
     "title=<substring>, or the app-specific activate. Alt+Tab is not "
     "reliable to script."),

    # --- more app recipes -----------------------------------
    ("system", "Windows Terminal / PowerShell window: open_app 'windows "
     "terminal' or 'powershell'; then ui_control type the command and key "
     "'{ENTER}'. Or just use the powershell tool directly - faster and "
     "structured."),
    ("system", "Calculator, Notepad, Paint, Snipping Tool, Task Manager, "
     "Control Panel, Settings all open by those plain names via open_app."),
    ("system", "Take a screenshot of the user's screen: that's not "
     "possible - computer_screenshot only sees Alfred's isolated desktop. "
     "Tell the user to press Win+Shift+S themselves."),
    ("system", "Volume: media keys via ui_control key "
     "'{VOLUME_UP}' / '{VOLUME_DOWN}' / '{VOLUME_MUTE}', or the app's own "
     "control. System volume also: (New-Object -ComObject WScript.Shell)."
     "SendKeys is unreliable - prefer the media keys."),
    ("system", "Open a URL in the default browser: Start-Process "
     "'https://...'. Open a file or folder: Start-Process '<path>' or "
     "explorer '<path>'."),

    # --- Alfred operational -------------------------------
    ("correction", "run_task is for jobs that need several ordered steps. "
     "For a single quick action (open one app, one powershell query, one "
     "click) just do it directly - don't spin up a task."),
    ("correction", "If a tool result is status='needs_confirmation', tell "
     "the user what it does and the risk, and only call the tool again "
     "with _confirmed:true if they agree. status='refused' means never - "
     "explain and suggest they do it by hand."),
    ("system", "After a multi-step task, Alfred reports only what it "
     "actually verified. 'Partly done' with a reason is honest - it does "
     "not mean nothing happened."),
]


def _providers():
    from google import genai

    from src.ai.providers import build_providers
    from src.config import load_settings

    s = load_settings()
    return build_providers(s, genai.Client(api_key=s.gemini_api_key)), s


def cmd_seed(_args: list[str]) -> int:
    from src.memory.learner import MemoryLearner
    from src.memory.store import MemoryStore

    providers, _ = _providers()
    store = MemoryStore(_DB)
    learner = MemoryLearner(store, providers.chat, providers.embedder)

    existing = {
        f.content.strip().lower()
        for f in store.all_facts()
        if f.source == _SOURCE
    }
    added = 0
    for category, content in WINDOWS_PLAYBOOK:
        if content.strip().lower() in existing:
            continue
        learner.remember(
            content=content, category=category, confidence=0.9,
            source=_SOURCE,
        )
        added += 1

    store.close()
    print(f"playbook: {added} new, {len(WINDOWS_PLAYBOOK)} total.")
    return 0


def cmd_list(_args: list[str]) -> int:
    for i, (cat, content) in enumerate(WINDOWS_PLAYBOOK, 1):
        print(f"  {i:2}. [{cat}] {content}")
    return 0


def cmd_clear(_args: list[str]) -> int:
    from src.memory.store import MemoryStore

    store = MemoryStore(_DB)
    n = 0
    for f in store.all_facts():
        if f.source == _SOURCE:
            store.delete_fact(f.id)
            n += 1
    store.close()
    print(f"removed {n} playbook fact(s).")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    handler = {"seed": cmd_seed, "list": cmd_list, "clear": cmd_clear}.get(argv[0])
    if handler is None:
        print(__doc__)
        return 2
    return handler(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
