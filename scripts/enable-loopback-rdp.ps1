<#
    Alfred - enable Remote Desktop for LOOPBACK ONLY.

    Why: a Windows "child session" (Alfred's own isolated desktop) is a
    loopback RDP session, so the Remote Desktop host has to be running.
    This script turns it on WITHOUT making the machine reachable over the
    network: the built-in Remote Desktop firewall rules stay disabled and
    a single rule allows 3389 from 127.0.0.1 only.

    Run this in an ADMIN PowerShell:
        powershell -ExecutionPolicy Bypass -File .\scripts\enable-loopback-rdp.ps1

    To reverse everything:
        powershell -ExecutionPolicy Bypass -File .\scripts\enable-loopback-rdp.ps1 -Undo

    It prints what it is about to do and asks before changing anything.
#>

[CmdletBinding()]
param(
    [switch]$Undo,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$TsKey    = 'HKLM:\System\CurrentControlSet\Control\Terminal Server'
$RuleName = 'Alfred child session (loopback only)'

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $pr.IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host "This needs an ADMIN PowerShell." -ForegroundColor Red
        Write-Host "Right-click PowerShell > Run as administrator, then re-run."
        exit 1
    }
}

function Confirm-Or-Exit([string[]]$Plan) {
    Write-Host ""
    Write-Host "About to:" -ForegroundColor Cyan
    $Plan | ForEach-Object { Write-Host "  - $_" }
    Write-Host ""
    if ($Force) { return }
    $answer = Read-Host "Proceed? (y/N)"
    if ($answer -notmatch '^(y|yes)$') {
        Write-Host "Nothing changed."
        exit 0
    }
}

Assert-Admin

# ------------------------------------------------------------------ undo
if ($Undo) {
    Confirm-Or-Exit @(
        "set fDenyTSConnections = 1  (turn the Remote Desktop host back off)",
        "remove the firewall rule '$RuleName'"
    )

    Set-ItemProperty -Path $TsKey -Name fDenyTSConnections -Value 1
    Write-Host "Remote Desktop host disabled." -ForegroundColor Green

    Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
    Write-Host "Firewall rule removed." -ForegroundColor Green

    Write-Host ""
    Write-Host "Note: the built-in 'Remote Desktop' firewall group was left"
    Write-Host "as this script found it. Check it in Windows Firewall if you"
    Write-Host "want to be certain nothing else opened 3389."
    exit 0
}

# ------------------------------------------------------------------ apply
$current = (Get-ItemProperty -Path $TsKey -Name fDenyTSConnections `
             -ErrorAction SilentlyContinue).fDenyTSConnections

Confirm-Or-Exit @(
    "set fDenyTSConnections = 0  (currently: $current) - starts the RDP host",
    "DISABLE the built-in 'Remote Desktop' firewall group (blocks network RDP)",
    "ADD one rule '$RuleName' allowing TCP 3389 from 127.0.0.1 ONLY",
    "start the TermService service if it is not already running"
)

# 1. Turn the Remote Desktop host on.
Set-ItemProperty -Path $TsKey -Name fDenyTSConnections -Value 0
Write-Host "[1/4] Remote Desktop host enabled." -ForegroundColor Green

# 2. Make sure the stock rules are NOT allowing this in from the network.
$group = Get-NetFirewallRule -DisplayGroup 'Remote Desktop' `
            -ErrorAction SilentlyContinue
if ($group) {
    $group | Disable-NetFirewallRule
    Write-Host "[2/4] Built-in Remote Desktop firewall rules disabled." `
        -ForegroundColor Green
} else {
    Write-Host "[2/4] No built-in Remote Desktop rules found (fine)." `
        -ForegroundColor Yellow
}

# 3. Allow loopback only.
Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule
New-NetFirewallRule `
    -DisplayName $RuleName `
    -Description 'Lets Alfred create an isolated child session. Loopback only.' `
    -Direction Inbound -Protocol TCP -LocalPort 3389 `
    -RemoteAddress 127.0.0.1 -Action Allow -Profile Any | Out-Null
Write-Host "[3/4] Loopback-only rule added (127.0.0.1 -> TCP 3389)." `
    -ForegroundColor Green

# 4. The listener only appears once the service is up.
$svc = Get-Service TermService
if ($svc.Status -ne 'Running') {
    Start-Service TermService
    Write-Host "[4/4] TermService started." -ForegroundColor Green
} else {
    Write-Host "[4/4] TermService already running." -ForegroundColor Green
}

# ---------------------------------------------------------------- verify
Write-Host ""
Write-Host "Verifying..." -ForegroundColor Cyan
Start-Sleep -Seconds 2

$listening = Test-NetConnection -ComputerName 127.0.0.1 -Port 3389 `
    -InformationLevel Quiet -WarningAction SilentlyContinue

if ($listening) {
    Write-Host "  Loopback RDP is listening. " -ForegroundColor Green -NoNewline
    Write-Host "Good."
} else {
    Write-Host "  Nothing on 127.0.0.1:3389 yet." -ForegroundColor Yellow
    Write-Host "  A reboot usually settles this. Re-run the probe afterwards."
}

Write-Host ""
Write-Host "Next: python -m src.childsession probe" -ForegroundColor Cyan
Write-Host "Undo any time with:  ...\enable-loopback-rdp.ps1 -Undo"
