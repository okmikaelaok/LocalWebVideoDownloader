param([switch]$SkipPythonSetup)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw "Install the .NET 8 SDK for Windows, then run this script again: https://dotnet.microsoft.com/download/dotnet/8.0"
}
$Sdks = & dotnet --list-sdks
if (-not ($Sdks | Where-Object { $_ -match '^(8|9|[1-9][0-9])\.' })) {
    throw "A .NET 8 or newer SDK is required to build LocalWebVideoDownloader."
}
$Runtimes = & dotnet --list-runtimes
if (-not ($Runtimes | Where-Object { $_ -match '^Microsoft.WindowsDesktop.App 8\.' })) {
    throw "Install the .NET 8 Desktop Runtime for Windows. The desktop app targets .NET 8."
}
if (-not $SkipPythonSetup) {
    & (Join-Path $PSScriptRoot "setup.ps1")
}

# Keep development caches out of the user's global tool directories.
$env:DOTNET_CLI_HOME = Join-Path $ProjectRoot ".dotnet-cli"
$env:NUGET_PACKAGES = Join-Path $ProjectRoot ".nuget\packages"
$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
$env:DOTNET_GENERATE_ASPNET_CERTIFICATE = "false"
$Project = Join-Path $ProjectRoot "desktop\CaptureBrowser\CaptureBrowser.csproj"
& dotnet restore $Project --locked-mode
if ($LASTEXITCODE -ne 0) { throw "Could not restore the WebView2 SDK package from NuGet." }
& dotnet build $Project -c Release --no-restore
if ($LASTEXITCODE -ne 0) { throw "The LocalWebVideoDownloader build failed." }

Write-Host "LocalWebVideoDownloader setup complete. Launch with:"
Write-Host "  Double-click 'START_LocalWebVideoDownloader.cmd' in the project folder."
Write-Host "  Developers can also run: .\scripts\run-desktop.ps1"
Write-Host "If the browser reports a missing WebView2 Runtime, install Microsoft's Evergreen Runtime."
