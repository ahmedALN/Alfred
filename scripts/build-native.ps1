<#
.SYNOPSIS
    Builds Alfred's native helpers.

.DESCRIPTION
    Two C# projects sit behind Alfred's desktop control:

      ChildInputAgent   input, screen capture, app lifecycle and - since
                        the accessibility layer moved in - UI Automation,
                        running INSIDE whichever session it serves.
      ChildSessionProbe the host that creates Alfred's private session
                        and keeps it alive.

    Both are usually running when you come to rebuild them, and a running
    exe cannot be overwritten - the build fails a screenful of retries
    later with MSB3027. This stops them first and offers to start the
    agent again afterwards.

.PARAMETER SkipStop
    Leave running processes alone. The build will fail if any hold the
    exe open.

.PARAMETER NoRestart
    Do not start the agent again when the build finishes.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\build-native.ps1
#>

[CmdletBinding()]
param(
    [switch]$SkipStop,
    [switch]$NoRestart
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$projects = @(
    @{ Name = 'ChildInputAgent';   Path = "$root\src\windows\native\ChildInputAgent" },
    @{ Name = 'ChildSessionProbe'; Path = "$root\src\windows\native\ChildSessionProbe" }
)

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    Write-Error "dotnet SDK not found. Install it, then run this again."
}

# ---------------------------------------------------------------- stop
$wasRunning = $false

if (-not $SkipStop) {
    foreach ($name in 'ChildInputAgent', 'ChildSessionProbe') {
        $running = Get-Process -Name $name -ErrorAction SilentlyContinue

        if ($running) {
            if ($name -eq 'ChildInputAgent') { $wasRunning = $true }
            Write-Host "Stopping $name ($($running.Count) process(es))..."
            $running | Stop-Process -Force -ErrorAction SilentlyContinue
        }
    }

    # Windows releases the file lock a moment after the process dies.
    Start-Sleep -Milliseconds 800
}

# --------------------------------------------------------------- build
$failed = @()

foreach ($project in $projects) {
    if (-not (Test-Path $project.Path)) {
        Write-Warning "$($project.Name): no project at $($project.Path) - skipped."
        continue
    }

    Write-Host ""
    Write-Host "Building $($project.Name)..." -ForegroundColor Cyan

    & dotnet build $project.Path -c Release --nologo -v quiet

    if ($LASTEXITCODE -ne 0) {
        $failed += $project.Name
        Write-Host "  FAILED" -ForegroundColor Red
    }
    else {
        Write-Host "  ok" -ForegroundColor Green
    }
}

Write-Host ""

if ($failed.Count -gt 0) {
    Write-Error "Build failed: $($failed -join ', ')"
}

Write-Host "Both native helpers built." -ForegroundColor Green

# ------------------------------------------------------------- restart
# The agent normally starts from the AlfredChildAgent logon trigger. That
# only fires at logon, so after a rebuild this session would sit without
# one until the next sign-in.
if ($wasRunning -and -not $NoRestart) {
    $exe = "$root\src\windows\native\ChildInputAgent\bin\Release\" +
           "net10.0-windows10.0.26100.0\ChildInputAgent.exe"

    if (Test-Path $exe) {
        Write-Host "Starting ChildInputAgent again..."

        # Via the scheduled task, which is how it starts normally: it
        # runs fully detached, with the same logging. Starting the exe
        # from here instead leaves it holding this console's handles,
        # and the script never returns.
        $task = Get-ScheduledTask -TaskName 'AlfredChildAgent' `
            -ErrorAction SilentlyContinue

        if ($task) {
            Start-ScheduledTask -TaskName 'AlfredChildAgent'
            Write-Host "  started" -ForegroundColor Green
        }
        else {
            Write-Warning ("No AlfredChildAgent task registered. Run " +
                "scripts\install-child-agent-task.ps1, or start the " +
                "agent yourself:")
            Write-Host "    $exe"
        }
    }
}

Write-Host ""
Write-Host "Note: Alfred's private session keeps its own agent, started by"
Write-Host "the AlfredChildAgent logon trigger. It picks up the new build"
Write-Host "the next time that session is created - Alfred recycles a"
Write-Host "session whose agent has gone, so this happens on its own."
