param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-p]{32}$')]
    [string]$ExtensionId,

    [ValidateSet("Chrome", "Edge", "Both")]
    [string]$Browser = "Chrome"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$NativeExecutable = Join-Path $ProjectRoot ".venv\Scripts\local-web-video-downloader-native.exe"
if (-not (Test-Path -LiteralPath $NativeExecutable)) {
    throw "The native bridge is not installed. Run .\scripts\setup.ps1 first."
}

$HostName = "com.localwebvideodownloader.bridge"
$HostDirectory = Join-Path $env:LOCALAPPDATA "LocalWebVideoDownloader"
$ManifestPath = Join-Path $HostDirectory "$HostName.json"
New-Item -ItemType Directory -Path $HostDirectory -Force | Out-Null

$Manifest = [ordered]@{
    name = $HostName
    description = "Local bridge from the LocalWebVideoDownloader companion to the desktop application"
    path = $NativeExecutable
    type = "stdio"
    allowed_origins = @("chrome-extension://$ExtensionId/")
}
$Json = $Manifest | ConvertTo-Json -Depth 4
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($ManifestPath, $Json, $Utf8NoBom)

$RegistryRoots = @()
if ($Browser -in @("Chrome", "Both")) {
    $RegistryRoots += "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$HostName"
}
if ($Browser -in @("Edge", "Both")) {
    $RegistryRoots += "HKCU:\Software\Microsoft\Edge\NativeMessagingHosts\$HostName"
}

foreach ($RegistryPath in $RegistryRoots) {
    New-Item -Path $RegistryPath -Force | Out-Null
    Set-Item -Path $RegistryPath -Value $ManifestPath
}

Write-Host "Registered the native bridge for $Browser."
Write-Host "Manifest: $ManifestPath"
Write-Host "Reload the unpacked extension from the browser extensions page."
