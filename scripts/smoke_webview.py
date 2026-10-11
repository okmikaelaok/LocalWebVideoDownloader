"""Exercise the actual WebView2 control with resources served entirely in-process."""

from __future__ import annotations

import io
import json
import subprocess
import tkinter as tk
from dataclasses import replace
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from localwebvideodownloader.browser_capture import CapturedSource
from localwebvideodownloader.gui import DownloaderWindow
from localwebvideodownloader.models import CaptionTrack, InspectionResult, MediaFormat, MediaItem
from localwebvideodownloader.webview_browser import browser_executable, read_browser_event


def check_ui_handoff(capture: CapturedSource) -> None:
    """Use real withdrawn Tk widgets, with no public metadata requests or downloads."""
    root = tk.Tk()
    root.withdraw()
    try:
        app = DownloaderWindow(root)
        root.update_idletasks()
        # Busy work must keep the latest selection without interrupting the operation.
        with (
            patch.object(root, "deiconify"),
            patch.object(root, "lift"),
            patch.object(app, "_start_inspection") as start,
        ):
            app._set_busy("inspection")
            app.events.put(("browser_capture", replace(capture, url=capture.url + "?older=1")))
            app.events.put(("browser_capture", capture))
            app._poll_events()
            assert app.pending_capture is capture
            start.assert_not_called()
            app._set_busy(None)
            app._poll_events()
            assert app.pending_capture is None and app.capture_context is capture
            assert app.url_var.get() == capture.url
            start.assert_called_once()

        track = CaptionTrack("en", extension="vtt")
        result = InspectionResult(
            capture.url,
            items=(
                MediaItem(
                    key="fixture",
                    title="Fixture video",
                    media_id="fixture",
                    source_url=capture.url,
                    formats=(MediaFormat("720", height=720, video_codec="h264"),),
                    captions=(track,),
                ),
            ),
        )
        # Inspect and download must use the same backend with the player's Referer.
        with patch("localwebvideodownloader.gui.YtDlpBackend") as backend_factory:
            backend = backend_factory.return_value
            backend.inspect.return_value = result
            app._set_busy("inspection")
            app._inspection_worker(capture.url, capture)
            app._poll_events()
            backend_factory.assert_called_once_with(referer_url=capture.referer_url)
            backend.inspect.assert_called_once_with(capture.url)
            assert app.backend is backend and app.inspection is result
            assert app.busy_mode is None and app.format_rows["format-1"] == "720"
            assert app.caption_rows == [track]
            assert str(app.download_button["state"]) == "normal"

        session = Mock()
        app.browser_session = session
        with patch.object(root, "destroy") as destroy:
            app._on_close()
            session.close.assert_called_once()
            destroy.assert_called_once()
    finally:
        root.destroy()


def main() -> int:
    result = subprocess.run(
        [str(browser_executable()), "--smoke-test"],
        input=b"",
        capture_output=True,
        timeout=40,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    events = [json.loads(line) for line in result.stdout.splitlines()]
    completed = [event for event in events if event.get("event") == "smoke_complete"]
    captures = [event for event in events if event.get("event") == "capture"]
    assert result.returncode == 0, f"Browser failed with exit code {result.returncode}"
    assert completed and completed[-1]["ok"], "Browser did not detect the fixture media"
    assert captures, "Browser did not send a selected capture over stdout"
    # .invalid is intentionally synthetic. Only DNS validation is replaced here;
    # the real WebView2 fixture is intercepted in process and never uses the network.
    with patch(
        "localwebvideodownloader.security.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("1.1.1.1", 443))],
    ):
        event = read_browser_event(io.BytesIO(json.dumps(captures[0]).encode() + b"\n"))
    assert event and event[0] == "capture", "Python rejected the native capture schema"
    assert urlsplit(event[1].frame_url or "").hostname == "player-fixture.invalid", (
        "Cross-origin player provenance was lost"
    )
    assert isinstance(event[1], CapturedSource)
    check_ui_handoff(event[1])
    print(
        "Desktop smoke passed: cross-origin frame, extensionless HLS MIME, direct video, "
        "Python handoff, queued selection, quality/caption UI, and close propagation."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
