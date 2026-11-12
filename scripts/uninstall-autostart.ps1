#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$TaskName = "Switch2DiscordPresence"
)

$ErrorActionPreference = "Stop"
schtasks.exe /Delete /TN $TaskName /F
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Could not delete task '$TaskName' (it may not exist)."
} else {
    Write-Host "Autostart task removed."
}
