# Claude Command Center launcher for native Windows PowerShell.
#
# Usage:
#   .\run.ps1                       # port 8090
#   $env:PORT = "9000"; .\run.ps1
#   .\run.ps1 --app                 # open as a Chromium app window

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigLocalEnv = Join-Path $env:USERPROFILE ".claude\command-center\config.local.env"
$ServiceLogDir = Join-Path $env:USERPROFILE ".claude\command-center\logs"

function Import-LocalEnv {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        $idx = $trimmed.IndexOf("=")
        if ($idx -lt 1) {
            continue
        }
        $key = $trimmed.Substring(0, $idx).Trim()
        $value = $trimmed.Substring($idx + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
}

function Resolve-Python {
    $candidates = @("python", "py")
    foreach ($candidate in $candidates) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            return $cmd.Source
        }
    }
    throw "python not found on PATH. Install Python 3, then re-run .\run.ps1."
}

function Show-Help {
    @"
Usage: .\run.ps1 [OPTION]

  (no args)    Run CCC in the foreground
  --app        Open the dashboard in a Chromium app window
  --help, -h   Show this help

Windows service install is not implemented yet. Keep this PowerShell window
open, or run CCC under your preferred process manager.
"@
}

function Open-AppWindow {
    param([string]$Url)

    $browserNames = @("chrome.exe", "msedge.exe", "brave.exe", "chromium.exe")
    foreach ($name in $browserNames) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            Start-Process -FilePath $cmd.Source -ArgumentList @("--app=$Url", "--window-size=1400,900")
            return
        }
    }

    Start-Process $Url
}

Import-LocalEnv -Path $ConfigLocalEnv

if (-not $env:PORT) {
    $env:PORT = "8090"
}

switch ($RemainingArgs[0]) {
    "--help" {
        Show-Help
        exit 0
    }
    "-h" {
        Show-Help
        exit 0
    }
    "--app" {
        $hostName = if ($env:CCC_APP_HOST) { $env:CCC_APP_HOST } else { "127.0.0.1" }
        $url = if ($env:CCC_APP_URL) { $env:CCC_APP_URL } else { "http://${hostName}:$env:PORT" }
        Open-AppWindow -Url $url
        exit 0
    }
    "--install-service" {
        Write-Error "Windows service install is not implemented yet. Run .\run.ps1 in the foreground or under your preferred process manager."
        exit 2
    }
    "--uninstall-service" {
        Write-Error "Windows service install is not implemented yet."
        exit 2
    }
    "--service-status" {
        Write-Output "CCC Windows service: not installed (native Windows service support is not implemented yet)."
        exit 0
    }
    { $_ -and $_.StartsWith("-") } {
        Write-Error "Unknown option: $_"
        Show-Help
        exit 2
    }
}

New-Item -ItemType Directory -Force -Path $ServiceLogDir | Out-Null
$python = Resolve-Python

Write-Output "-> Command Center"
Write-Output "  port     : $env:PORT"
Write-Output "  bind     : $(if ($env:CCC_BIND_HOST) { $env:CCC_BIND_HOST } else { '(default 127.0.0.1, or from network.json)' })"
Write-Output "  url      : http://localhost:$env:PORT"

& $python (Join-Path $Here "server.py")
exit $LASTEXITCODE
