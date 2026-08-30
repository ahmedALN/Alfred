<#
    Alfred - deliver the input agent into the isolated child session.

    Why this needs admin: getting a process to start inside ANOTHER
    Windows session is privileged. The only non-elevated route is the
    HKCU Run key, and on this machine that did not fire for our entry
    (other Run entries did). A logon-triggered scheduled task is the
    reliable mechanism - creating it needs admin ONCE; after that it
    runs automatically whenever a session starts, with no prompts.

    What it creates:
      Task name : AlfredChildAgent
      Trigger   : at logon (which includes a child session starting)
      Runs as   : you, non-elevated, in whatever session logged on
      Action    : ChildInputAgent.exe, which writes its own
                  logs\child-agent.s<session>.log so we can see any crash

    Run in an ADMIN PowerShell:
        powershell -ExecutionPolicy Bypass -File "C:\Users\ahmed\Alfred\scripts\install-child-agent-task.ps1"

    Remove it completely:
        ... \install-child-agent-task.ps1 -Undo
#>

[CmdletBinding()]
param([switch]$Undo, [switch]$Force)

$ErrorActionPreference = 'Stop'
$TaskName = 'AlfredChildAgent'
$Root     = Split-Path -Parent $PSScriptRoot
$LogDir   = Join-Path $Root 'logs'

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $pr.IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host "This needs an ADMIN PowerShell." -ForegroundColor Red
        exit 1
    }
}

Assert-Admin

if ($Undo) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false `
        -ErrorAction SilentlyContinue
    Write-Host "Task '$TaskName' removed." -ForegroundColor Green
    exit 0
}

$exe = Get-ChildItem -Path (Join-Path $Root 'src\windows\native\ChildInputAgent\bin\Release') `
        -Filter 'ChildInputAgent.exe' -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName

if (-not $exe) {
    Write-Host "ChildInputAgent.exe is not built. Run:" -ForegroundColor Red
    Write-Host "  dotnet build src\windows\native\ChildInputAgent -c Release"
    exit 1
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

Write-Host ""
Write-Host "About to create a scheduled task:" -ForegroundColor Cyan
Write-Host "  name    : $TaskName"
Write-Host "  trigger : at logon (a child session starting counts)"
Write-Host "  runs as : $env:USERNAME, NOT elevated"
Write-Host "  action  : $exe"
Write-Host ("  log     : " + (Join-Path $LogDir 'child-agent.s<session>.log'))
Write-Host ""
if (-not $Force) {
    if ((Read-Host "Proceed? (y/N)") -notmatch '^(y|yes)$') {
        Write-Host "Nothing changed."; exit 0
    }
}

# The agent writes its own per-session log now, so it runs directly.
#
# It used to be wrapped in cmd with '>> child-agent.log'. With an agent
# in the user's session AND one in Alfred's, the first held that file
# and the second could not redirect to it - cmd exited 1, no agent
# started, and isolation failed with "session did not become ready".
$action = New-ScheduledTaskAction -Execute $exe

$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
    -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances Parallel `
    -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action `
    -Trigger $trigger -Principal $principal -Settings $settings -Force |
    Out-Null

Write-Host "Task registered." -ForegroundColor Green
Write-Host ""
Write-Host "Next: recycle Alfred's session so the task fires, then check" `
    -ForegroundColor Cyan
Write-Host "  python -m src.childsession agents"
Write-Host "Undo: ...\install-child-agent-task.ps1 -Undo"
