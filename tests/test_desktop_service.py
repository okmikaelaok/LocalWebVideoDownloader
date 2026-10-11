from __future__ import annotations

import io
import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from localwebvideodownloader.desktop_service import (
    MAX_DESKTOP_MESSAGE_BYTES,
    DesktopController,
    encode_command,
    read_event,
)
from localwebvideodownloader.errors import InspectionError, ValidationError
from localwebvideodownloader.models import (
    CaptionTrack,
    DownloadResult,
    InspectionResult,
    MediaFormat,
    MediaItem,
    ProgressEvent,
)

URL = "https://1.1.1.1/watch?token=secret"
ITEM = MediaItem(
    key="fixture",
    media_id="fixture",
    title="My: video",
    source_url=URL,
    formats=(MediaFormat("720", height=720, video_codec="h264", audio_codec="aac"),),
    captions=(CaptionTrack("en", extension="vtt"), CaptionTrack("en", extension="srt")),
)


def event(kind: str, **values: object) -> dict[str, object]:
    return {"version": 1, "event": kind, **values}


class DesktopProtocolTests(unittest.TestCase):
    def test_frames_are_bounded_and_versioned(self) -> None:
        self.assertEqual(read_event(io.BytesIO(b'{"version":1,"event":"ready"}\n')), event("ready"))
        self.assertIsNone(read_event(io.BytesIO()))
        for payload in (
            b"{broken}\n",
            b"{}",
            b"[]\n",
            b"\xff\n",
            b"x" * (MAX_DESKTOP_MESSAGE_BYTES + 1),
            b'{"version":true,"event":"ready"}\n',
            b'{"version":1,"event":[]}\n',
            b'{"version":2,"event":"ready"}\n',
            b'{"version":1,"event":"execute_shell"}\n',
        ):
            with self.subTest(payload=payload[:40]), self.assertRaises(ValidationError):
                read_event(io.BytesIO(payload))

    def test_command_limit_and_no_embedded_newline_framing(self) -> None:
        encoded = encode_command("init", value="line\nline")
        self.assertEqual(encoded.count(b"\n"), 1)
        self.assertEqual(json.loads(encoded)["version"], 1)
        with self.assertRaises(ValidationError):
            encode_command("inspection_complete", items=["x" * MAX_DESKTOP_MESSAGE_BYTES])


class DesktopControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.messages: list[dict] = []
        self.backend = Mock()
        self.backend.inspect.return_value = InspectionResult(URL, (ITEM,))
        self.backend.download.return_value = DownloadResult((Path("Saved.mp4"),), None)
        self.factory = Mock(return_value=self.backend)
        self.controller = DesktopController(
            lambda command, **values: self.messages.append({"command": command, **values}),
            backend_factory=self.factory,
        )

    def tearDown(self) -> None:
        self.controller.close()
        if self.controller.worker:
            self.controller.worker.join(timeout=2)

    def wait_idle(self) -> None:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            worker = self.controller.worker
            if worker:
                worker.join(timeout=0.05)
            with self.controller.lock:
                if self.controller.busy is None:
                    return
        self.fail("Fixture operation did not finish")

    def inspect(self) -> None:
        self.controller.handle(event("inspect", url=URL))
        self.wait_idle()

    def download(self, **values: object) -> None:
        fields = dict(
            item_index=0,
            format_id="720",
            caption_indices=[0],
            output_directory="downloads",
            output_name="My renamed video",
            embed_captions=False,
        )
        self.controller.handle(event("download", **(fields | values)))

    def test_capture_referrer_and_metadata_name_are_preserved(self) -> None:
        self.controller.handle(
            event(
                "inspect",
                url=URL,
                capture={
                    "url": URL,
                    "page_url": "https://8.8.8.8/page",
                    "frame_url": "https://8.8.4.4/player",
                    "kind": "hls",
                },
            )
        )
        self.wait_idle()
        self.factory.assert_called_once_with(referer_url="https://8.8.4.4/player")
        metadata = next(m for m in self.messages if m["command"] == "inspection_complete")
        self.assertEqual(metadata["items"][0]["suggested_name"], "My_ video")
        self.assertNotIn("token=secret", json.dumps(metadata))
        self.download()
        self.wait_idle()
        request = self.backend.download.call_args.args[0]
        self.assertIs(request.item, ITEM)
        self.assertEqual(request.output_name, "My renamed video")
        self.assertEqual(request.captions, (ITEM.captions[0],))
        self.assertTrue(any(m["command"] == "download_complete" for m in self.messages))

    def test_invalid_selections_cannot_start_downloads(self) -> None:
        self.inspect()
        for values in (
            {"item_index": True},
            {"item_index": 99},
            {"caption_indices": [True]},
            {"caption_indices": [0, 0]},
            {"caption_indices": [0, 1]},
            {"embed_captions": True, "caption_indices": []},
            {"embed_captions": "yes"},
            {"output_name": "../escape"},
            {"output_directory": ""},
            {"audio_format_id": ["720"]},
        ):
            with self.subTest(values=values):
                self.download(**values)
                self.assertEqual(self.messages[-2]["command"], "error")
        self.backend.download.assert_not_called()

    def test_failed_inspection_discards_previous_selection(self) -> None:
        self.inspect()
        self.backend.inspect.side_effect = InspectionError("Try the Browser tab.")
        self.inspect()
        self.assertIsNone(self.controller.inspection)
        self.download()
        self.backend.download.assert_not_called()

    def test_mismatched_or_private_capture_never_reaches_backend(self) -> None:
        for source in ("http://127.0.0.1/stream", "https://8.8.8.8/other"):
            self.controller.handle(
                event(
                    "inspect",
                    url=URL,
                    capture={
                        "url": source,
                        "page_url": "https://8.8.8.8/page",
                        "kind": "direct",
                    },
                )
            )
            self.wait_idle()
        self.backend.inspect.assert_not_called()

    def test_latest_capture_waits_until_download_finishes(self) -> None:
        self.inspect()
        entered = threading.Event()
        release = threading.Event()

        def downloading(*_args: object, **_kwargs: object) -> DownloadResult:
            entered.set()
            release.wait(timeout=2)
            return DownloadResult((), None)

        self.backend.download.side_effect = downloading
        self.download()
        self.assertTrue(entered.wait(timeout=1))
        try:
            self.controller.handle(event("inspect", url="https://1.1.1.1/older"))
            self.controller.handle(event("inspect", url="https://1.1.1.1/latest"))
            self.assertEqual(self.backend.inspect.call_count, 1)
        finally:
            release.set()
        self.wait_idle()
        self.backend.inspect.assert_called_with("https://1.1.1.1/latest")
        self.assertEqual(self.backend.inspect.call_count, 2)

    def test_cancel_and_close_signal_active_worker(self) -> None:
        self.controller.busy = "download"
        self.controller.pending_inspection = {"url": URL}
        self.controller.handle(event("cancel"))
        self.assertTrue(self.controller.cancel_event.is_set())
        self.assertIsNone(self.controller.pending_inspection)
        self.controller.close()
        self.controller.handle(event("inspect", url=URL))
        self.backend.inspect.assert_not_called()

    def test_shutdown_signal_does_not_wait_for_controller_lock(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def hold_lock() -> None:
            with self.controller.lock:
                entered.set()
                release.wait(timeout=2)

        holder = threading.Thread(target=hold_lock)
        holder.start()
        self.assertTrue(entered.wait(timeout=1))
        try:
            self.controller.signal_close()
            self.assertTrue(self.controller.closed.is_set())
            self.assertTrue(self.controller.cancel_event.is_set())
        finally:
            release.set()
            holder.join(timeout=2)

    def test_progress_is_throttled_and_redacts_secrets(self) -> None:
        for _ in range(100):
            self.controller._progress(ProgressEvent("downloading", 1, 10))
        self.assertEqual(len(self.messages), 1)
        self.controller._progress(ProgressEvent("processing", message=URL))
        self.assertNotIn("token=secret", json.dumps(self.messages))


class LauncherTests(unittest.TestCase):
    @patch("localwebvideodownloader.desktop.browser_executable", return_value=Path("browser.exe"))
    @patch("localwebvideodownloader.desktop.subprocess.Popen")
    def test_launches_single_native_window_not_tk(self, popen: Mock, _exe: Mock) -> None:
        from localwebvideodownloader.desktop import main

        class RecordingPipe(io.BytesIO):
            snapshot = b""

            def close(self) -> None:
                self.snapshot = self.getvalue()
                super().close()

        process = popen.return_value
        process.stdin = RecordingPipe()
        process.stdout = io.BytesIO(b'{"version":1,"event":"ready"}\n')
        process.poll.return_value = None
        process.wait.return_value = 0
        with patch("localwebvideodownloader.gui.main") as legacy_gui:
            self.assertEqual(main(), 0)
            legacy_gui.assert_not_called()
        self.assertEqual(popen.call_args.args[0], ["browser.exe", "--desktop-stdio"])
        messages = [json.loads(line) for line in process.stdin.snapshot.splitlines()]
        self.assertIsNone(messages[0]["initial_url"])
        self.assertEqual(messages[1]["command"], "init")


if __name__ == "__main__":
    unittest.main()
