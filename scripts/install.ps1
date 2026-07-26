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
$WatchtowerRepoUrl = "https://github.com/amirfish1/watchtower"
$WatchtowerInstallDir = Join-Path $env:USERPROFILE ".ccc\watchtower"
$WatchtowerTarballUrl = "https://github.com/amirfish1/watchtower/archive/refs/heads/main.tar.gz"
$WatchtowerPypiName = "watchtower-cli"
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

# WatchTower is CCC's queue engine. Without it the dashboard silently drops
# worker dispatch, plan-to-fleet import, and delivery receipts, so install it
# into the SAME interpreter that runs server.py (CCC needs `import watchtower`
# in-process). Never fatal: CCC still boots on its built-in fallback engine.
function Install-Watchtower {
    param([string]$Python)

    & $Python -c "import watchtower" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Output "install: WatchTower already installed - skipping."
        return
    }

    & $Python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "python is below WatchTower's 3.11 minimum - skipping WatchTower. CCC will use its built-in queue engine (no worker dispatch, no plan import)."
        return
    }

    $source = $null
    if (Test-Path -LiteralPath (Join-Path $WatchtowerInstallDir ".git")) {
        git -C $WatchtowerInstallDir pull --ff-only 2>$null | Out-Null
        $source = $WatchtowerInstallDir
    } elseif (-not (Test-Path -LiteralPath $WatchtowerInstallDir)) {
        Write-Output "install: fetching WatchTower (CCC's queue engine) from $WatchtowerRepoUrl"
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $WatchtowerInstallDir) | Out-Null
        git clone --depth 1 $WatchtowerRepoUrl $WatchtowerInstallDir 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $source = $WatchtowerInstallDir
        } else {
            Remove-Item -Recurse -Force -LiteralPath $WatchtowerInstallDir -ErrorAction SilentlyContinue
        }
    }

    if ($source) {
        Write-Output "install: installing WatchTower from $source"
        & $Python -m pip install --user --quiet -e $source 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Output "install: WatchTower installed - watchtower.queue is now available."
            return
        }
        Write-Output "install: editable install failed; falling back to a source tarball"
    }

    Write-Output "install: installing WatchTower from $WatchtowerTarballUrl"
    & $Python -m pip install --user --quiet $WatchtowerTarballUrl 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Output "install: WatchTower installed - watchtower.queue is now available."
        return
    }

    # PyPI last: the published release lags the repo, so it is a floor.
    Write-Output "install: installing WatchTower from PyPI ($WatchtowerPypiName)"
    & $Python -m pip install --user --quiet $WatchtowerPypiName 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Output "install: WatchTower installed - watchtower.queue is now available."
        return
    }

    Write-Warning "could not install WatchTower. CCC falls back to its built-in queue engine - tickets can be filed but no worker is dispatched. Retry manually: $Python -m pip install --user $WatchtowerPypiName"
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
$python = Resolve-Python
Warn-IfNoClaudeCli

$channel = Resolve-Channel -Raw $From
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $SourceFile) | Out-Null
Set-Content -LiteralPath $SourceFile -Value $channel -Encoding UTF8
Write-Output "install: attribution channel = $channel"

Sync-Repo
Install-Watchtower -Python $python

$env:PORT = [string]$Port
$dashboardUrl = "http://localhost:$Port"
Write-Output "install: launching .\run.ps1 on port $Port"
Write-Output "install: keep this PowerShell window open while CCC is running."
Open-WhenReady -TargetPort $Port -Url $dashboardUrl
Set-Location -LiteralPath $InstallDir
& (Join-Path $InstallDir "run.ps1")
exit $LASTEXITCODE
