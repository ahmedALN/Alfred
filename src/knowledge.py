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

    # --- working inside an app ------------------------------------
    ("correction", "Multi-step work inside an app follows one shape: "
     "open_app, then ui_control wait_ready (apps take seconds to paint - "
     "a tree read immediately after launch comes back empty), then "
     "ui_control tree once, then act by ref or name, then ui_control get "
     "to read the result back."),
    ("correction", "In a busy app use ui_control tree with contains='<word>' "
     "instead of reading the whole tree - the control you want is often "
     "past the listing limit otherwise."),
    ("correction", "If APP NOTES already give a control name for this app, "
     "use it directly and skip the exploratory tree. Only read the tree if "
     "that name turns out to be gone."),
    ("correction", "ALFRED NEVER TYPES PASSWORDS, PINs, card numbers or "
     "security codes. If a task needs a sign-in, get the app to the "
     "sign-in screen, tell the user it's ready and ask them to enter it "
     "(or use their password manager), then continue once they confirm. "
     "ui_control refuses password fields outright."),
    ("system", "ui_control actions: windows, focus, tree, find, click, "
     "double_click, right_click, invoke, type, key, get, select, expand, "
     "scroll, menu, wait_for, wait_ready, exists."),
    ("system", "ui_control select is for combo boxes, dropdowns, list items "
     "and tabs; expand opens a tree node or dropdown; menu takes a path "
     "like 'File->Save As'; scroll takes direction up/down/left/right."),

    # --- app recipes ----------------------------------------------
    ("system", "Spotify: open it, ui_control tree, type the artist/song "
     "into the 'Search' Edit control, then click the 'Play' button (or "
     "the first result). Confirm with ui_control get on the now-playing "
     "text. Ctrl+L also focuses the Spotify search box."),
    ("system", "Steam: the Library tab lists installed games; a game is "
     "launched by selecting it in the library and clicking 'Play'. Steam "
     "takes a while to become usable after launching - use wait_ready."),
    ("system", "A game's own menus are usually NOT in the accessibility "
     "tree (they are drawn by the game engine). ui_control tree will come "
     "back nearly empty - that is the signal to fall back to "
     "desktop_control, or to use the game's own keyboard shortcuts."),
    ("system", "Launchers (Steam, Epic, Battle.net, Xbox) are ordinary "
     "apps with real accessibility trees even when the games they launch "
     "are not - do the launcher part with ui_control."),
    ("correction", "Reach for the whole-job ui_control actions before "
     "the single gestures: after open_app and wait_ready, 'search "
     "window= text=' types into the app's own search box and submits, "
     "and 'open_item window= name=' opens a result, library row, tile, "
     "save or instance by name. 'Open Steam, search Hades, open it' is "
     "three calls. tree/click/type is the fallback, not the default."),
    ("correction", "If wait_ready fails, read windows_open in the result "
     "before retrying. A 'Sign in to ...' or update window means the app "
     "needs the USER, not more waiting - say so and hand it to them. "
     "Never type credentials."),
    ("system", "Games and launchers usually have no Start-menu entry - "
     "open_app finds them by their desktop shortcut instead, so 'steam', "
     "'multimc', 'brawlhalla' and 'fortnite' all resolve by name."),

    ("correction", "If a window reports only System / Minimise / "
     "Maximise / Close, it paints its own interface and publishes no "
     "accessibility tree - Roblox and virtually every game are like "
     "this, and wait_ready says so with renders_own_ui. Do not retry, "
     "wait longer or read deeper; there is nothing to find. Use "
     "desktop_control, or do the job on the app's website instead."),
    ("system", "For Roblox specifically, the website is accessible where "
     "the client is not: open roblox.com, search there, and pressing "
     "Play on a game's page hands off to the installed client. Same "
     "trick for any launcher with a web equivalent."),
    ("correction", "When wait_ready returns needs_user, STOP and ask. "
     "'choose_profile' comes with the actual account names - read them "
     "out and let the user pick, then open_item that name. 'sign_in' "
     "means tell them which app wants signing in and wait. Never guess "
     "an account and never type credentials."),
    ("correction", "Before working in an app, clear what is in the way. "
     "ui_control clear_popups closes promos and splash windows on its "
     "own - Steam's 'Special Offers' is a separate window that sits "
     "between you and the search box - and REPORTS anything that is "
     "actually a decision. search and open_item do this automatically "
     "when they cannot find their target."),
    ("correction", "An update prompt is a decision, not an obstacle. "
     "MultiMC opens with 'A new update is available!' and that window "
     "swallows clicks meant for the instance list, which is why "
     "double-clicking an instance appeared to do nothing. Ask the user "
     "whether to update, then act on their answer - never update an app "
     "on your own initiative, and never dismiss the question silently."),
    ("system", "MultiMC: double-clicking an instance launches it, once "
     "nothing is covering the window. The Launch button in the right "
     "panel has no accessible name so it cannot be clicked by name; the "
     "instance row can. MultiMC.exe --launch <instance> also works."),
    ("system", "When a window reports far more elements than it has "
     "named controls, its buttons are drawn without labels - normal for "
     "Qt apps and game launchers. ui_control says so in the not_found "
     "instruction. Those controls cannot be found by name at all, so "
     "reach for a keyboard route, the row itself, or the app's command "
     "line rather than hunting."),

    ("system", "A control with no name can still be used, once. "
     "ui_control 'unnamed window=' lists the unlabelled controls with "
     "their positions; click one with 'click x= y=', watch what changed, "
     "and record it with 'learn_control name= x= y='. From then on "
     "open_item finds it by name. Positions are stored as a fraction of "
     "the window, so they survive it being moved or resized."),
    ("correction", "Never click an unnamed control at random during a "
     "real task - in MultiMC one of them is Delete. Probe only when the "
     "user has asked you to learn the app, and if nothing observable "
     "happens after a click, ASK them what it did rather than guessing "
     "a label."),
    ("system", "MultiMC's right-hand panel, top to bottom: Change Group, "
     "Launch, Launch Offline, Edit Instance, Edit Notes, View Mods, View "
     "Worlds, Manage Screenshots, Minecraft Folder, Config Folder, "
     "Instance Folder, Create Shortcut, Export Instance, Delete, Copy "
     "Instance. None of them has an accessible name."),

    ("correction", "Once inside an app's sub-window - a settings dialog, "
     "an instance editor, a preferences pane - read its tree separately "
     "by its own title. Controls that were unnamed in the main window "
     "are very often properly named in there, so drop back to plain "
     "click-by-name rather than carrying on with positions."),
    ("system", "MultiMC: 'Add Instance' creates one (pick a source, type "
     "the Name, PICK A VERSION explicitly, OK). Select an instance and "
     "open_item 'Edit Instance' to open 'Console window for <name>', "
     "where the Version tab has Install Fabric / Forge / NeoForge / "
     "Quilt - click one, pick the loader version from the list, OK. "
     "Confirm by re-reading the tab for 'Fabric Loader'."),
    ("correction", "To add a mod file, prefer copying the .jar into the "
     "instance's mods folder with powershell over driving a file-open "
     "dialog. 'View Folder' on the Loader mods tab opens the right "
     "place. A file copy is exact; a file dialog is several fragile "
     "steps that can silently land elsewhere."),

    ("correction", "A Chromium app - Steam, Discord, Spotify, anything "
     "Electron - can report a completely EMPTY tree while looking "
     "perfectly normal on screen: it switches accessibility off when "
     "nothing has asked for it. ui_control retries and waits for it, but "
     "if a window you know is populated comes back with nothing, that is "
     "the reason. Read it again before concluding anything."),

    # --- knowing what kind of app you are looking at -------------------
    ("correction", "Work out what kind of app it is BEFORE deciding how "
     "to drive it. Read the tree once and count the named controls. "
     "Many (a dozen or more): drive everything by name. A handful, "
     "usually just a menu bar: go through the menus and keyboard "
     "shortcuts. Almost none but plenty of elements: the buttons are "
     "drawn without labels - map them once. Nothing but System, "
     "Minimise, Maximise, Close: it paints its own interface and only a "
     "screenshot can see it."),
    ("system", "Surveyed on this machine. Drive by name: Spotify (250 "
     "named controls), Discord (178), Stremio (118), Docker Desktop "
     "(74), Steam, and any browser. Menus only: VLC - its transport bar "
     "is mapped instead. Positions needed: MultiMC's right-hand panel. "
     "Nothing readable: Roblox, and games generally."),
    ("system", "VLC: the menu bar is named (Media, Playback, Audio, "
     "Video, Subtitle, Tools, View, Help). The transport bar has no "
     "names and is mapped: Play, Previous, Stop, Next, Fullscreen, "
     "Extended settings, Playlist, Loop, Shuffle, Mute, Volume. The "
     "keyboard is often better still - Space play/pause, F fullscreen, "
     "M mute."),
    ("correction", "Games do not expose an interface at all, so do not "
     "try to click your way through one. Start them from the launcher, "
     "which usually IS readable: a game's Steam page has a Play button, "
     "MultiMC launches on a double-click, and roblox.com works where the "
     "Roblox client does not."),
    ("correction", "An app that has been mapped is remembered. Check "
     "what is already known before working anything out again - the app "
     "profile lists both the controls seen and any buttons learned by "
     "position, and open_item accepts those names directly."),

    # --- the web ------------------------------------------------------
    ("correction", "To reach a specific web page, pass the URL straight to "
     "open_app: open_app app='https://www.youtube.com/@Deji/videos'. It "
     "opens in the default browser, already on that page. Do NOT open a "
     "browser and then try to drive its address bar - that is several "
     "fragile steps to arrive where one reliable step lands you."),
    ("correction", "Never type into a browser without naming the field. "
     "ui_control type with only a window types into whatever has focus, "
     "which in a browser is the page, not the search box. Read the tree, "
     "find the field, and pass into=<ref>."),
    ("system", "If you really must use the address bar, focus it with "
     "ui_control key '^l' first, then type, then '{ENTER}'. Ctrl+L works "
     "in every mainstream browser."),
    ("correction", "A web page's accessibility tree fills in AFTER the "
     "window appears - a YouTube channel reports 18 controls one moment "
     "and 173 the next. Always ui_control wait_ready with "
     "min_controls=40 before reading a page, or you will read it "
     "half-built and conclude the content is not there."),
    ("correction", "On a website, ui_control tree with the default "
     "limit=80 stops before the content: a site's first 80 controls are "
     "its navigation. Pass limit=300 (or contains=) when reading a page."),
    ("system", "A YouTube channel's uploads live at "
     "https://www.youtube.com/@<handle>/videos, newest first. To play the "
     "latest: open_app that URL, wait_ready min_controls=40, tree "
     "limit=300, then click the FIRST Hyperlink whose name is long (a "
     "video title, usually ending '<n> minutes') - skip 'Go to channel "
     "...', 'Home', 'Shorts' and other navigation."),
    ("system", "A YouTube video page exposes real player controls: "
     "'Play', 'Pause (k)', 'Mute (m)', 'Full screen (f)', a 'Volume' "
     "slider. After opening a video, confirm it is playing by finding "
     "'Pause' - and if only 'Play' responds, click it. The window title "
     "becomes the video title, which is how you report what started."),
    ("correction", "If a page's tree stays tiny (under ~20 controls) even "
     "after wait_ready, that browser is not exposing its content to the "
     "accessibility layer. Do not keep guessing at coordinates - open the "
     "same URL in Chrome instead (powershell Start-Process chrome.exe "
     "-ArgumentList '<url>'), whose tree is reliable."),
    ("correction", "Searching a site by typing its name into a search box "
     "is the slow path. Most sites take a query in the URL - YouTube: "
     "https://www.youtube.com/results?search_query=<terms>. Build the URL "
     "and open_app it."),
    ("correction", "'Open/play the latest video' is not finished when the "
     "channel page is showing. Click the video itself, then confirm - the "
     "window title becomes the video title, and a Pause control appears "
     "once it is playing. Report what actually started playing."),
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
