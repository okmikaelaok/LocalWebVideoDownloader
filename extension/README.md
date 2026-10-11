# LocalWebVideoDownloader browser companion

This Manifest V3 extension detects ordinary non-DRM media requested by the active Chrome or Edge
tab and hands one selected source to LocalWebVideoDownloader's optional legacy Python interface.
It is a discovery companion, not a downloader by itself. The default single-window desktop app
instead has its own Browser tab and needs no extension.

## Load the unpacked extension

1. From the repository root, run `.\scripts\setup.ps1` in PowerShell. This installs the desktop
   application and native bridge executable.
2. In Chrome, open `chrome://extensions`; in Edge, open `edge://extensions`.
3. Enable **Developer mode**.
4. Choose **Load unpacked** and select this `extension` directory.
5. Copy the 32-character extension ID shown on the extensions page.
6. Register that exact ID from the repository root, choosing the browser you use:

   ```powershell
   .\scripts\register-native-host.ps1 -ExtensionId "your-extension-id" -Browser Chrome
   ```

   Use `Edge` instead of `Chrome`, or `Both` when the displayed unpacked ID is the same in both
   browsers.
7. Reload the extension from the browser extensions page, then pin it to the toolbar if desired.

The exact extension ID must be listed in the desktop native-host manifest. If it changes, run the
registration command again with the new ID. Native-host registration is currently Windows-only.

## Use it

1. Open a page containing a video.
2. Open the extension and select **Start detection**.
3. Approve the optional website-access request. Third-party iframe players and CDNs use origins
   other than the page itself, so `activeTab` access alone cannot observe their media requests.
4. Press Play on the page. If the source loaded before detection began, reload the page and play it
   again. Detection remains active across a same-origin reload and stops on a cross-origin
   navigation or when you select **Stop**.
5. Select an HLS, DASH, direct-video, browser-media, or embedded-player result.
6. Select **Open in desktop app**. The browser requests the optional native-messaging permission
   only at this point, then sends the selected capture to the local application.
7. Choose an available quality and captions, edit the base filename if wanted, and download in
   the Python interface. A base filename should not include a path or extension.

Manifests are normally the most useful candidates because they can describe multiple qualities,
audio tracks, and captions. Individual HLS/DASH segment files are deliberately ignored.

## Privacy and permissions

- Detection starts only after an explicit click and records requests only for that tab.
- Broad HTTP(S) host access is optional but is necessary to see cross-origin iframe and CDN
  requests. The extension discards events from tabs that the user has not armed. Once granted,
  that permission remains in browser settings until the user revokes it; stopping detection clears
  captured state but does not revoke the permission itself.
- Exact capture URLs may contain short-lived signatures. They are kept only in
  `chrome.storage.session`, capped at 64 candidates, removed on tab closure, and cleared when the
  browser session ends.
- The popup displays only URL origins and paths. Query strings and fragments are never rendered.
- The extension never reads or sends cookies, authorization headers, request headers, request
  bodies, response bodies, or page source. It examines only the response `Content-Type` value for
  classification and does not retain the raw response headers.
- Only the selected URL and bounded page/frame provenance are sent to the registered local native
  host. There is no telemetry or remote application service.
- The extension does not decrypt media, capture license responses, defeat DRM, or bypass access
  controls. The desktop application performs its own URL, protocol, live-stream, and DRM checks.

Download only media that you are authorized to save.

## Detection limits

- A request that completed before detection started cannot be reconstructed; reload or replay it.
- `blob:` and `data:` URLs are ignored. For Media Source Extensions players, detection depends on
  seeing the underlying HTTP(S) manifest or media request.
- WebSocket messages and media hidden entirely inside encrypted or proprietary application
  traffic are not inspected.
- Cookie-bound or authenticated sources remain unsupported because browser credentials are not
  exported to the desktop application.
- The desktop app receives only an origin-level Referer, not browser cookies or authorization
  state, so a candidate shown in the popup may still fail inspection.
- Short-lived signed media URLs can expire before inspection or download; replay the video to
  capture a fresh request.
- Browser and store policy pages such as `chrome://` and `edge://` cannot be inspected.

## Files

```text
extension/
|-- manifest.json       Manifest V3 permissions and entry points
|-- service-worker.js   Per-tab network observation, ephemeral state, and native handoff
|-- detector.js         Rendered DOM and Performance API discovery injected into page frames
|-- popup.html          Extension popup markup
|-- popup.js            Permission prompts and sanitized candidate interface
|-- popup.css           Popup presentation
`-- README.md           Installation, use, privacy, and development notes
```

The extension contains no remotely hosted code and requires no JavaScript build step.

## Development checks

From the repository root, validate the manifest and JavaScript syntax with locally available
tools:

```powershell
python -m json.tool extension\manifest.json > $null
node --check extension\service-worker.js
node --check extension\detector.js
node --check extension\popup.js
```

After changing permissions or the manifest, reload the unpacked extension from the browser's
extensions page.
