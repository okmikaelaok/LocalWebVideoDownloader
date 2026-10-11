# LocalWebVideoDownloader

A Windows desktop app for downloading supported, non-DRM web videos. Paste a link, choose video quality and audio, add optional captions, and save locally.

⬇️ [Download](#download) | 🖼️ [Gallery](#gallery) | ⭐ [Current Main Features](#current-main-features) | 📝 [Notes](#notes)

## Download

A standalone installer is not available yet. Download or clone this repository, then follow the setup below.

### Requirements

- Windows 10 or 11.
- Python 3.11 or newer (3.12 recommended).
- [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0).
- [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/).
- FFmpeg and ffprobe on `PATH` for merging video/audio, embedding captions, and validating downloads.

### Setup and launch

Open PowerShell in the project folder and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup-desktop.ps1
```

After setup, double-click **START_LocalWebVideoDownloader.cmd**. The app runs without a background console window.

Downloads go to **VideosDownloaded** inside the project folder. The folder is created on your first download, and you can change the output folder in the app.

## Gallery

**Download tab** - equal-width, scrollable video and audio lists with a compact captions dropdown.

![Download tab](<preview_pics/s_01.png>)

**Browser tab** - open a page and discover its loaded media sources.

![Browser tab](<preview_pics/s_02.png>)

<details>
<summary>More screenshots</summary>

**Completed download** - the full green bar and Completed message stay visible.

![Completed download](<preview_pics/s_04.png>)

**Source detection** - media sources discovered in the built-in browser.

![Media source detection](<preview_pics/s_03.png>)

**Launcher and download folder** - start the app from the project directory.

![Project folder and launcher](<preview_pics/s_00.png>)

</details>

## Current Main Features

- Inspect supported page, player, direct-video, HLS, and DASH URLs.
- Choose automatic best quality or a specific video and audio format.
- Select one optional caption track, saved separately or embedded with FFmpeg.
- Rename files and choose where to save them.
- Cancel downloads and resume partial files when the source supports it.
- Keep the full green progress bar and **Completed** message after downloading.
- Discover media through the built-in **Browser** tab.
- Use the optional command-line interface, legacy Python UI, or [Chrome/Edge companion](extension/README.md).

## How to use

1. Paste a link in the **Download** tab and click **Inspect**.
2. Choose the detected video, quality, and audio.
3. Choose captions from the dropdown, or leave **No captions** selected.
4. Edit **Save as** and the output folder if needed.
5. Click **Download**.

If inspection cannot find the video, click **Open in browser**, open/play the page, and use **Start detection** or **Scan page**. Select a detected source and click **Inspect selected**.

## Notes

- Download only videos you own or are authorized to save. The app does not bypass DRM, paywalls, or access controls.
- Cookies and login sessions are not imported. Protected or expired links may fail.
- Live recording, encrypted HLS, and `blob:` URLs without a detectable HTTP source are not supported. Website compatibility varies.
- The app has no telemetry or local web server. Its browser uses a separate InPrivate WebView2 session.
- Enter a base filename in **Save as**; the extension is added automatically. Existing completed files are not silently overwritten.
- A cancelled download may leave a `.part` file for resuming later.
- Startup errors appear in a dialog and are logged to `build/desktop-launch.log`.
- After native desktop code changes, close the app and rebuild with `.\scripts\setup-desktop.ps1 -SkipPythonSetup`.
- The native executable needs the Python service; launch using the CMD file rather than the executable directly.

Check installed tools:

```powershell
.\.venv\Scripts\local-web-video-downloader.exe doctor
```

For architecture, build details, and developer checks, see [desktop/README.md](desktop/README.md). For command-line options, run:

```powershell
.\.venv\Scripts\local-web-video-downloader.exe --help
```

## License

Licensed under **GNU AGPLv3 only** (`AGPL-3.0-only`). See [LICENSE](LICENSE). Third-party dependencies retain their own licenses.
