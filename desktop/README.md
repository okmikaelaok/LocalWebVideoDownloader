# LocalWebVideoDownloader (desktop app)

The dedicated Windows app has one C# WinForms window with **Download** and **Browser** tabs.
Download controls and WebView2 share that window; Python runs inspection and downloads in the
background. It does not run a web server or require a browser extension. Opening the app shows
Download without initializing WebView2 or loading a website.

## Setup and launch

Install Python 3.11+, the .NET 8 SDK for Windows (or a newer SDK plus the .NET 8 Desktop Runtime),
and Microsoft's WebView2 Evergreen Runtime. FFmpeg/ffprobe remain needed for separate-stream
merging, caption embedding and output validation.

From PowerShell in the project root:

```powershell
.\scripts\setup-desktop.ps1
.\scripts\run-desktop.ps1
```

If scripts are disabled by PowerShell, use a process-only execution-policy setting:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup-desktop.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-desktop.ps1
```

Only the launch command is needed after setup. This does not change the machine's execution policy.

After C# edits, rebuild with `.\scripts\setup-desktop.ps1 -SkipPythonSetup`.
The native executable is built to
`desktop\CaptureBrowser\bin\Release\net8.0-windows\LocalWebVideoDownloader.exe`.
Launch through Python or `run-desktop.ps1`, so the process pipes are connected correctly.

## Workflow

1. Paste a URL into the **Download** tab and click **Inspect**.
2. Choose available quality, audio, captions, output folder, and an editable base filename.
3. If the site requires playback, click **Open in browser** to use the **Browser** tab. Opening a
   page there arms detection; play the video to expose its media requests. A response MIME type
   can reveal a manifest even without a familiar extension.
4. Select a source and click **Inspect selected** to inspect it on Download. **Inspect page URL**
   instead uses the existing static/extractor path.

Only one inspection or download runs at a time. Switching tabs does not stop browser detection;
use **Stop detection** when finished. Closing the window closes the browser and stops its Python
worker. The old Tkinter interface remains available through `run-gui.ps1` and optional extension
handoffs, but is not started by the dedicated desktop launcher.

The **File name** field is prefilled from the title. Enter a base name without an extension or
path; the downloader adds the actual media extension and caption language suffixes. Clearing the
field restores automatic title-and-ID naming. Invalid Windows names are rejected, and completed
files are not silently overwritten. Resume a matching partial download using the same filename,
folder, and media selection.

## Architecture

```text
One WinForms window
  Download tab <---- selected source ---- Browser tab (on demand)
       |                                  |
       |                    WebView2 network + DOM discovery
       |                                  |
       |                       bounded candidate catalog
       |
       +---- anonymous JSON pipes ---- Python desktop service
                                             |
                               URL/schema/filename validation
                                             |
                                yt-dlp + FFmpeg/ffprobe
```

`MediaCatalog.cs` classifies and ranks HLS, DASH, direct media, and embedded-player candidates.
`BrowserForm.cs` owns the shared window, tab navigation, and browser detection. `DownloadPanel.cs`
owns the native inspection, format/caption selection, filename, download, and progress controls.
`Program.cs` handles startup and bounded messages. `desktop.py` starts the application and
`desktop_service.py` handles the inspection/download worker and desktop IPC. `filenames.py`
validates literal basenames and builds safe media/caption output templates.

`browser_capture.py` shares the existing public-URL capture schema with the optional extension.
`webview_browser.py` retains the older capture-only protocol used by the optional Python interface
and browser smoke test. Package names, native-host IDs, and the existing executable filename have
not been renamed.

No page-to-native messaging or host objects are exposed. Only native controls can request a
handoff. Network observation retains URL, content type and document provenance (falling back to
the observed Referer origin for cross-process frames); it does not
retrieve response bodies or export cookies/authentication. Candidates cap at 64 and provenance
entries at 128. URL length is capped at 8192 characters. Desktop request/event lines are bounded to
1 MiB to accommodate metadata. The legacy capture-only protocol keeps its 64 KiB limit; extension
native messaging remains separately framed and validated. Neither protocol uses a listening port.

WebView2 uses an application-specific InPrivate profile, separate from Chrome/Edge. Runtime files
may remain in `%LOCALAPPDATA%\LocalWebVideoDownloader\WebView2`. Candidate URLs are held in memory;
the source list hides paths and queries, while the editable page/source URL inputs show their
actual values. No captured URLs appear on child process command lines.

Browser browsing uses WebView2's normal network stack; the Python public-network policy is applied
when selecting a source for download. This app is not intended as a public download service.

## Verification

```powershell
$env:DOTNET_CLI_HOME = Join-Path (Get-Location) '.dotnet-cli'
$env:NUGET_PACKAGES = Join-Path (Get-Location) '.nuget\packages'
dotnet run --project .\desktop\CaptureBrowser.Tests\CaptureBrowser.Tests.csproj -c Release
node .\desktop\CaptureBrowser.Tests\dom-scan.test.cjs
.\.venv\Scripts\python.exe .\scripts\smoke_unified_desktop.py
.\.venv\Scripts\python.exe .\scripts\smoke_webview.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The unified smoke test drives the real native Download tab and Python worker through their normal
pipes, using a fake media backend so it performs no network or file writes. It verifies one-window
startup, lazy browser initialization, filename/caption selections, progress, and completion. The
browser smoke test starts a hidden real WebView2 control. Its fixed cross-origin iframe/CDN fixture
and media responses are served in process, without fetching a public video. It verifies
extensionless HLS MIME detection, direct video, and the capture protocol consumed by Python. It
does not prove that any particular external player works or that a detected source can be
downloaded. Python tests also cover filename validation, literal output naming, and the desktop
worker protocol. Legacy Tk widget checks exercise the optional Python interface rather than the
default native window.

## Current limits

One native window; no installer yet. Captions and alternate qualities must be present in
the selected source's metadata. Cookie-bound media, login-state export, DRM, protected/encrypted
streams and live recording remain outside this iteration. Page popups, permission requests and
downloads initiated by the browser itself are blocked. Candidate classification uses heuristics
and deliberately excludes likely fragments, including TS/AAC URLs. DOM access to cross-origin
frames remains restricted; their network responses provide the primary discovery signal.

API references: [WebView2 request/response events](https://learn.microsoft.com/en-us/microsoft-edge/webview2/how-to/webresourcerequested),
[WebView2 security guidance](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/security).
