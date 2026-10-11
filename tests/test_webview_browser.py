from __future__ import annotations

import io
import json
import unittest
from unittest.mock import MagicMock, patch

from localwebvideodownloader.browser_capture import CapturedSource
from localwebvideodownloader.errors import ValidationError
from localwebvideodownloader.webview_browser import BrowserSession, read_browser_event


def line(value: object) -> bytes:
    return json.dumps(value).encode() + b"\n"


class BrowserProtocolTests(unittest.TestCase):
    def test_capture_is_validated_and_signed_query_is_preserved(self) -> None:
        event = read_browser_event(
            io.BytesIO(
                line(
                    {
                        "version": 1,
                        "event": "capture",
                        "capture": {
                            "url": "https://1.1.1.1/media.m3u8?sig=secret",
                            "page_url": "https://8.8.8.8/watch",
                            "frame_url": None,
                            "kind": "hls",
                        },
                    }
                )
            )
        )
        self.assertEqual(event[0], "capture")
        self.assertIsInstance(event[1], CapturedSource)
        self.assertTrue(event[1].url.endswith("?sig=secret"))

    def test_private_candidate_is_recoverable_and_not_returned(self) -> None:
        event = read_browser_event(
            io.BytesIO(
                line(
                    {
                        "version": 1,
                        "event": "capture",
                        "capture": {
                            "url": "http://127.0.0.1/media.mp4?secret=yes",
                            "page_url": "https://1.1.1.1/watch",
                            "kind": "direct",
                        },
                    }
                )
            )
        )
        self.assertEqual(event[0], "error")
        self.assertNotIn("secret", str(event[1]))

    def test_rejects_broken_framing_and_versions(self) -> None:
        for payload in (
            b"x" * 65537,
            b"{}",
            b"{broken}\n",
            line({"version": True, "event": "ready"}),
            line({"version": 9, "event": "ready"}),
        ):
            with self.subTest(size=len(payload)), self.assertRaises(ValidationError):
                read_browser_event(io.BytesIO(payload))

    def test_remote_error_text_cannot_reach_ui(self) -> None:
        event = read_browser_event(
            io.BytesIO(
                line(
                    {
                        "version": 1,
                        "event": "error",
                        "code": "https://secret.test/token",
                        "message": "password=secret",
                    }
                )
            )
        )
        self.assertNotIn("secret", str(event[1]))

    @patch("localwebvideodownloader.webview_browser.browser_executable", return_value="browser.exe")
    @patch("localwebvideodownloader.webview_browser.subprocess.Popen")
    def test_startup_uses_pipe_and_closes_child(self, popen: MagicMock, _exe: MagicMock) -> None:
        process = MagicMock()
        process.stdin = io.BytesIO()
        process.stdout = io.BytesIO(line({"version": 1, "event": "ready"}))
        process.poll.return_value = 0
        popen.return_value = process
        events = []
        BrowserSession(lambda kind, value: events.append(kind)).run(
            "https://1.1.1.1/watch?sig=secret"
        )
        self.assertNotIn("secret", str(popen.call_args.args))
        self.assertIn(b"secret", process.stdin.getvalue())
        self.assertEqual(events, ["ready", "closed"])
        process.wait.assert_called()


if __name__ == "__main__":
    unittest.main()
