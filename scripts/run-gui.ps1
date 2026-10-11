$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Launcher = Join-Path $ProjectRoot ".venv\Scripts\local-web-video-downloader-gui.exe"

if (-not (Test-Path -LiteralPath $Launcher)) {
    throw "The application is not installed. Run .\scripts\setup.ps1 first."
}

& $Launcher
