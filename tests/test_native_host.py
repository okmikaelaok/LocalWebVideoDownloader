from __future__ import annotations

import io
import json
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from localwebvideodownloader.browser_capture import CapturedSource
from localwebvideodownloader.errors import InvalidUrlError, ValidationError
from localwebvideodownloader.native_host import (
    handle_message,
    launch_desktop_capture,
    read_native_message,
    write_native_message,
)


def capture_payload() -> dict[str, object]:
    return {
        "version": 1,
        "command": "open_capture",
        "capture": {
            "url": "https://1.1.1.1/video/master.m3u8?token=secret",
            "page_url": "https://8.8.8.8/watch/123",
            "frame_url": "https://1.0.0.1/embed/123",
            "kind": "hls",
            "content_type": "application/vnd.apple.mpegurl",
        },
    }


class NativeFramingTests(unittest.TestCase):
    def test_round_trip_native_message(self) -> None:
        output = io.BytesIO()
        message = {"ok": True, "value": "hello"}
        write_native_message(output, message)
        output.seek(0)
        self.assertEqual(read_native_message(output), message)

    def test_rejects_oversized_header_before_reading_body(self) -> None:
        stream = io.BytesIO(struct.pack("=I", 70 * 1024))
        with self.assertRaises(ValidationError):
            read_native_message(stream)

    def test_rejects_truncated_body(self) -> None:
        stream = io.BytesIO(struct.pack("=I", 10) + b"{}")
        with self.assertRaises(ValidationError):
            read_native_message(stream)


class CaptureValidationTests(unittest.TestCase):
    def test_accepts_bounded_public_capture(self) -> None:
        capture = CapturedSource.from_native_message(capture_payload())
        self.assertEqual(capture.kind, "hls")
        self.assertEqual(capture.referer_url, "https://1.0.0.1/embed/123")

    def test_rejects_private_media_and_unknown_kind(self) -> None:
        private = capture_payload()
        private["capture"] = dict(private["capture"], url="http://127.0.0.1/video.mp4")
        with self.assertRaises(InvalidUrlError):
            CapturedSource.from_native_message(private)

        unknown = capture_payload()
        unknown["capture"] = dict(unknown["capture"], kind="license")
        with self.assertRaises(ValidationError):
            CapturedSource.from_native_message(unknown)

    @patch("localwebvideodownloader.native_host.launch_desktop_capture")
    def test_handle_message_returns_no_url_or_secret(self, launch: MagicMock) -> None:
        response = handle_message(capture_payload())
        self.assertTrue(response["ok"])
        self.assertNotIn("url", response)
        self.assertNotIn("secret", json.dumps(response))
        launch.assert_called_once()


class DesktopLaunchTests(unittest.TestCase):
    @patch("localwebvideodownloader.native_host.subprocess.Popen")
    def test_capture_is_piped_not_placed_on_command_line(self, popen: MagicMock) -> None:
        process = MagicMock()
        process.stdin = MagicMock()
        popen.return_value = process
        capture = CapturedSource.from_native_message(capture_payload())

        launch_desktop_capture(capture)

        command = popen.call_args.args[0]
        self.assertEqual(command[-2:], ["-m", "localwebvideodownloader.browser_launch"])
        self.assertNotIn("secret", " ".join(command))
        written = process.stdin.write.call_args.args[0]
        self.assertIn(b"secret", written)
        process.stdin.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
