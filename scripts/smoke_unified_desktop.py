"""Drive the real native UI and Python service through pipes, without fetching media."""

from __future__ import annotations

import io
import json
import subprocess
import threading
from pathlib import Path

from localwebvideodownloader.desktop_service import DesktopController, encode_command, read_event
from localwebvideodownloader.models import (
    CaptionTrack,
    DownloadRequest,
    DownloadResult,
    InspectionResult,
    MediaFormat,
    MediaItem,
    ProgressEvent,
)
from localwebvideodownloader.webview_browser import browser_executable

FIXTURE_URL = "https://1.1.1.1/native-desktop-fixture"


class FixtureBackend:
    """Only the extraction/network/filesystem layer is replaced in this UI test."""

    downloaded: DownloadRequest | None = None

    def __init__(self, *, referer_url: str | None = None) -> None:
        assert referer_url is None

    def inspect(self, url: str) -> InspectionResult:
        assert url == FIXTURE_URL
        return InspectionResult(
            url,
            (
                MediaItem(
                    key="native-fixture",
                    media_id="fixture",
                    title="Native fixture",
                    source_url=url,
                    formats=(
                        MediaFormat("720", height=720, video_codec="h264", audio_codec="aac"),
                    ),
                    captions=(CaptionTrack("en", extension="vtt"),),
                ),
            ),
        )

    def download(self, request: DownloadRequest, *, progress, cancel_event) -> DownloadResult:
        assert request.output_name == "Renamed 100% (native)", "Native filename edit was lost"
        assert request.item.source_url == FIXTURE_URL
        assert len(request.captions) == 1 and request.captions[0].language == "en"
        assert not cancel_event.is_set()
        FixtureBackend.downloaded = request
        progress(ProgressEvent("downloading", downloaded_bytes=50, total_bytes=100))
        return DownloadResult((Path("Renamed 100% (native).mp4"),), True)


def main() -> int:
    with subprocess.Popen(
        [str(browser_executable()), "--desktop-integration-test"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ) as process:
        assert process.stdin and process.stdout
        write_lock = threading.Lock()
        messages: list[tuple[str, dict]] = []

        def send(command: str, **values) -> None:
            messages.append((command, values))
            with write_lock:
                process.stdin.write(encode_command(command, **values))
                process.stdin.flush()

        controller = DesktopController(send, backend_factory=FixtureBackend)
        timeout = threading.Timer(25, process.kill)
        timeout.daemon = True
        timeout.start()
        complete = None
        try:
            process.stdin.write(b'{"version":1,"initial_url":null}\n')
            process.stdin.flush()
            while line := process.stdout.readline(1024 * 1024 + 1):
                value = json.loads(line)
                if value.get("event") == "desktop_smoke_complete":
                    complete = value
                    break
                # Use the actual production framing/schema boundary, not a test shortcut.
                message = read_event(io.BytesIO(line))
                assert message is not None
                controller.handle(message)
            assert complete and complete["ok"], "Unified UI did not complete its fixture workflow"
            assert complete["windows"] == 1, "More than one application window opened"
            assert complete["browser_initialized"] is False, (
                "Browser initialized without user action"
            )
            assert FixtureBackend.downloaded is not None, "Native UI never requested its download"
            assert not [values for command, values in messages if command == "error"], messages
            assert process.wait(timeout=5) == 0, "Native UI exited with an error"
        finally:
            timeout.cancel()
            controller.close()
            if controller.worker:
                controller.worker.join(timeout=2)
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    print(
        "Unified desktop smoke passed: one window, lazy browser, inspection, "
        "rename, captions, progress."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
