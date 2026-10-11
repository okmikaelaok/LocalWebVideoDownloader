@echo off
setlocal

cd /d "%~dp0"

if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo.
    echo LocalWebVideoDownloader could not start.
    echo.
    echo If this is your first time using the app, follow the setup steps in README.md.
    echo After setup, double-click this file again.
    echo.
    pause
    exit /b 1
)

start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0scripts\launch-desktop.pyw"

endlocal
