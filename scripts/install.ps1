# Claude Command Center one-command installer for native Windows PowerShell.
#
# Usage:
#   irm https://raw.githubusercontent.com/amirfish1/claude-command-center/main/scripts/install.ps1 | iex
#   .\scripts\install.ps1 -From readme

[CmdletBinding()]
param(
    [string]$From = $env:CCC_FROM,
    [int]$Port = $(if ($env:PORT) { [int]$env:PORT } else { 8090 })
)

$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/amirfish1/claude-command-center"
$InstallDir = Join-Path $env:USERPROFILE ".ccc\claude-command-center"
$SourceFile = Join-Path $env:USERPROFILE ".claude\command-center\install-source"
$ValidChannels = @("readme", "landing-hero", "hn", "ph", "devto", "yt", "gh-trending", "dmg", "unknown")

function Resolve-Channel {
    param([string]$Raw)
    if (-not $Raw) {
        return "unknown"
    }
    if ($ValidChannels -contains $Raw) {
        return $Raw
    }
    return "unknown"
}

function Require-Command {
    param(
        [string]$Name,
        [string]$InstallHint
    )
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name not found on PATH. $InstallHint"
    }
}

function Resolve-Python {
    foreach ($name in @("python", "py")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            return $cmd.Source
        }
    }
    throw "python not found on PATH. Install Python 3 from python.org or the Microsoft Store, then re-run this installer."
}

function Warn-IfNoClaudeCli {
    if (-not (Get-Command "claude" -ErrorAction SilentlyContinue)) {
        Write-Warning "claude CLI not on PATH. CCC will still start; install Claude Code if you want Claude sessions."
    }
}

function Sync-Repo {
    if (Test-Path -LiteralPath (Join-Path $InstallDir ".git")) {
        Write-Output "install: updating existing checkout at $InstallDir"
        git -C $InstallDir pull --ff-only
    } else {
        Write-Output "install: cloning $RepoUrl to $InstallDir"
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $InstallDir) | Out-Null
        git clone $RepoUrl $InstallDir
    }
}

function Open-WhenReady {
    param(
        [int]$TargetPort,
        [string]$Url
    )
    Start-Job -ScriptBlock {
        param($TargetPort, $Url)
        for ($i = 0; $i -lt 60; $i++) {
            try {
                $client = [Net.Sockets.TcpClient]::new()
                $async = $client.BeginConnect("127.0.0.1", $TargetPort, $null, $null)
                if ($async.AsyncWaitHandle.WaitOne(1000) -and $client.Connected) {
                    $client.Close()
                    Start-Process $Url
                    return
                }
                $client.Close()
            } catch {
            }
            Start-Sleep -Seconds 1
        }
    } -ArgumentList $TargetPort, $Url | Out-Null
}

Require-Command -Name "git" -InstallHint "Install Git for Windows, then re-run this installer."
$null = Resolve-Python
Warn-IfNoClaudeCli

$channel = Resolve-Channel -Raw $From
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $SourceFile) | Out-Null
Set-Content -LiteralPath $SourceFile -Value $channel -Encoding UTF8
Write-Output "install: attribution channel = $channel"

Sync-Repo

$env:PORT = [string]$Port
$dashboardUrl = "http://localhost:$Port"
Write-Output "install: launching .\run.ps1 on port $Port"
Write-Output "install: keep this PowerShell window open while CCC is running."
Open-WhenReady -TargetPort $Port -Url $dashboardUrl
Set-Location -LiteralPath $InstallDir
& (Join-Path $InstallDir "run.ps1")
exit $LASTEXITCODE
