#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$ExePath = "",
    [string]$TaskName = "Switch2DiscordPresence"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($ExePath)) {
    $ExePath = Join-Path $root "dist\Switch2Presence\Switch2Presence.exe"
}
$ExePath = (Resolve-Path -LiteralPath $ExePath).Path
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "Executable not found: $ExePath. Run scripts\build.ps1 first."
}

$action = "`"$ExePath`""
Write-Host "Registering logon task '$TaskName' -> $ExePath"
schtasks.exe /Create /SC ONLOGON /DELAY 0000:15 /RL LIMITED /TN $TaskName /TR $action /F
if ($LASTEXITCODE -ne 0) {
    throw "schtasks failed with exit code $LASTEXITCODE"
}
Write-Host "Autostart installed (15 s delay after logon)."
