<#
    Alfred - let the child session log in without prompting.

    READ THIS BEFORE RUNNING IT.

    The child session is a loopback RDP connection, so Windows wants
    credentials for it. Without a policy saying "you may reuse my current
    sign-in for this", you get a "Windows Security" password prompt every
    single time Alfred opens its session - which defeats the point.

    This script enables CredSSP *default credential delegation* for ONE
    target: TERMSRV/localhost. That means Windows may reuse your existing
    sign-in when connecting to Remote Desktop ON THIS MACHINE, and nothing
    else. It does not store your password anywhere, and it does not apply
    to any remote host.

    Honest note: credential delegation is a real security setting. Scoped
    to localhost it is about as contained as this gets, but if you would
    rather not enable it at all, the alternative is typing your password
    each time Alfred starts its session - which is a legitimate choice.

    Run in an ADMIN PowerShell:
        powershell -ExecutionPolicy Bypass -File "C:\Users\ahmed\Alfred\scripts\allow-childsession-logon.ps1"

    Reverse it completely:
        ... \allow-childsession-logon.ps1 -Undo
#>

[CmdletBinding()]
param(
    [switch]$Undo,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$Root   = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CredentialsDelegation'
$Sub    = Join-Path $Root 'AllowDefaultCredentials'
$Target = 'TERMSRV/localhost'

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $pr.IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host "This needs an ADMIN PowerShell." -ForegroundColor Red
        exit 1
    }
}

function Confirm-Or-Exit([string[]]$Plan) {
    Write-Host ""
    Write-Host "About to:" -ForegroundColor Cyan
    $Plan | ForEach-Object { Write-Host "  - $_" }
    Write-Host ""
    if ($Force) { return }
    if ((Read-Host "Proceed? (y/N)") -notmatch '^(y|yes)$') {
        Write-Host "Nothing changed."
        exit 0
    }
}

Assert-Admin

# ------------------------------------------------------------------ undo
if ($Undo) {
    Confirm-Or-Exit @(
        "remove the '$Target' entry from AllowDefaultCredentials",
        "turn AllowDefaultCredentials back off"
    )

    if (Test-Path $Sub) {
        Get-ItemProperty -Path $Sub |
            Get-Member -MemberType NoteProperty |
            Where-Object { $_.Name -match '^\d+$' } |
            ForEach-Object {
                $v = (Get-ItemProperty -Path $Sub -Name $_.Name).($_.Name)
                if ($v -eq $Target) {
                    Remove-ItemProperty -Path $Sub -Name $_.Name
                    Write-Host "Removed entry $($_.Name) ($Target)."
                }
            }
    }

    if (Test-Path $Root) {
        Set-ItemProperty -Path $Root -Name AllowDefaultCredentials -Value 0
    }

    Write-Host "Credential delegation disabled." -ForegroundColor Green
    Write-Host "A reboot (or gpupdate /force) makes it final."
    exit 0
}

# ------------------------------------------------------------------ apply
Confirm-Or-Exit @(
    "create/set $Root\AllowDefaultCredentials = 1",
    "add '$Target' to the allowed list (this machine's own RDP only)",
    "set ConcatenateDefaults_AllowDefault = 1 (keep any existing entries)"
)

New-Item -Path $Sub -Force | Out-Null
Set-ItemProperty -Path $Root -Name AllowDefaultCredentials -Value 1 -Type DWord
Set-ItemProperty -Path $Root -Name ConcatenateDefaults_AllowDefault `
    -Value 1 -Type DWord

# Find the next free numeric slot, unless the target is already listed.
$existing = @()
if (Test-Path $Sub) {
    $props = Get-ItemProperty -Path $Sub
    $existing = $props.PSObject.Properties |
        Where-Object { $_.Name -match '^\d+$' }
}

if ($existing | Where-Object { $_.Value -eq $Target }) {
    Write-Host "'$Target' was already allowed." -ForegroundColor Yellow
} else {
    $next = 1
    while ($existing.Name -contains "$next") { $next++ }
    Set-ItemProperty -Path $Sub -Name "$next" -Value $Target -Type String
    Write-Host "Allowed '$Target' (slot $next)." -ForegroundColor Green
}

gpupdate /target:computer /force | Out-Null

Write-Host ""
Write-Host "Done. Alfred's session should now log in without prompting." `
    -ForegroundColor Green
Write-Host "If it still prompts, a reboot settles the policy."
Write-Host ""
Write-Host "Undo any time:  ...\allow-childsession-logon.ps1 -Undo" `
    -ForegroundColor Cyan
