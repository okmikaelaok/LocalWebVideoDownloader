$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VirtualEnvironment = Join-Path $ProjectRoot ".venv"
$Python = Join-Path $VirtualEnvironment "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    $Created = $false
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($Selector in @("-3.12", "-3.11", "-3")) {
            & py $Selector -c "import sys; raise SystemExit(sys.version_info < (3, 11))" 2>$null
            if ($LASTEXITCODE -ne 0) {
                continue
            }
            & py $Selector -m venv $VirtualEnvironment
            if ($LASTEXITCODE -eq 0) {
                $Created = $true
                break
            }
        }
    }
    if (-not $Created -and (Get-Command python -ErrorAction SilentlyContinue)) {
        & python -c "import sys; raise SystemExit(sys.version_info < (3, 11))"
        if ($LASTEXITCODE -eq 0) {
            & python -m venv $VirtualEnvironment
            $Created = $LASTEXITCODE -eq 0
        }
    }
    if (-not $Created) {
        throw "Python 3.11 or newer is required to create the virtual environment."
    }
}

& $Python -c "import sys; raise SystemExit(sys.version_info < (3, 11))"
if ($LASTEXITCODE -ne 0) {
    throw "The existing .venv uses an unsupported Python. Recreate it with Python 3.11 or newer."
}

& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip."
}
& $Python -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install runtime dependencies."
}
& $Python -m pip install --no-deps -e $ProjectRoot
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install LocalWebVideoDownloader."
}

& $Python -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Tkinter is unavailable. The CLI and native desktop will work, but the legacy Python interface will not."
}

Write-Host "LocalWebVideoDownloader setup complete. Launch the legacy Python interface with:"
Write-Host "  .\.venv\Scripts\local-web-video-downloader-gui.exe"
