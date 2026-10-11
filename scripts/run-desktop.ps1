$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$Browser = Join-Path $ProjectRoot "desktop\CaptureBrowser\bin\Release\net8.0-windows\LocalWebVideoDownloader.exe"
if (-not (Test-Path -LiteralPath $Python) -or -not (Test-Path -LiteralPath $Browser)) {
    throw "Run .\scripts\setup-desktop.ps1 first to install Python dependencies and build LocalWebVideoDownloader."
}
$Launcher = Join-Path $PSScriptRoot "launch-desktop.pyw"
Start-Process -FilePath $Python -ArgumentList ('"' + $Launcher + '"') -WorkingDirectory $ProjectRoot -WindowStyle Hidden
