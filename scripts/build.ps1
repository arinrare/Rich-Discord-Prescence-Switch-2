#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    Write-Host "Creating Python 3.12 virtual environment..."
    py -3.12 -m venv .venv
}

$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not $SkipInstall) {
    Write-Host "Installing dependencies..."
    & $python -m pip install --upgrade pip
    & $python -m pip install -e ".[dev]"
}

Write-Host "Generating assets..."
& $python scripts\generate_assets.py

Write-Host "Building with PyInstaller..."
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --noconsole `
    --name Switch2Presence `
    --icon assets\switch2.ico `
    --add-data "assets;assets" `
    --paths src `
    --hidden-import pystray._win32 `
    --hidden-import winsdk.windows.media.ocr `
    --hidden-import winsdk.windows.graphics.imaging `
    --hidden-import winsdk.windows.globalization `
    --hidden-import winsdk.windows.storage.streams `
    --collect-submodules winsdk `
    scripts\entry.py

$exe = Join-Path $root "dist\Switch2Presence\Switch2Presence.exe"
if (Test-Path -LiteralPath $exe) {
    Write-Host ""
    Write-Host "Build complete: $exe"
    Write-Host "Install autostart with: powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1"
} else {
    throw "Build failed: $exe not found"
}
