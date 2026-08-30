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
      Action    : ChildInputAgent.exe, with output captured to
                  logs\child-agent.log so we can see any crash

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
$LogFile  = Join-Path $LogDir 'child-agent.log'

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
Write-Host "  log     : $LogFile"
Write-Host ""
if (-not $Force) {
    if ((Read-Host "Proceed? (y/N)") -notmatch '^(y|yes)$') {
        Write-Host "Nothing changed."; exit 0
    }
}

# cmd wrapper so stdout/stderr land in a log - if the agent dies inside
# the child session we need to see why, not just see it missing.
$action = New-ScheduledTaskAction -Execute 'cmd.exe' `
    -Argument ('/c ""{0}" >> "{1}" 2>&1"' -f $exe, $LogFile)

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
