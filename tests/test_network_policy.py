from __future__ import annotations

import importlib.util
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

if importlib.util.find_spec("yt_dlp"):
    from yt_dlp.networking.exceptions import RequestError
else:
    RequestError = RuntimeError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from localwebvideodownloader.ytdlp_backend import _restricted_youtube_dl_class


class _RedirectHandler(BaseHTTPRequestHandler):
    hits: list[str] = []

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        self.hits.append(self.path)
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", "/blocked")
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"should not be reached")


@unittest.skipUnless(importlib.util.find_spec("yt_dlp"), "yt-dlp is not installed")
class RestrictedYtDlpNetworkTests(unittest.TestCase):
    def test_redirect_target_is_checked_before_second_request(self) -> None:
        import yt_dlp

        _RedirectHandler.hits.clear()
        server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        calls: list[str] = []

        def policy(url: str, *, allow_private_network: bool = False) -> str:
            calls.append(url)
            if url.endswith("/blocked"):
                raise ValueError("blocked redirect")
            return url

        try:
            downloader_class = _restricted_youtube_dl_class(yt_dlp, allow_private_network=False)
            with (
                patch("localwebvideodownloader.ytdlp_backend.validate_network_url", side_effect=policy),
                downloader_class({"quiet": True, "no_warnings": True}) as downloader,
                self.assertRaises(RequestError),
            ):
                downloader.urlopen(f"http://127.0.0.1:{server.server_port}/start")
            self.assertTrue(any(url.endswith("/blocked") for url in calls))
            self.assertEqual(_RedirectHandler.hits, ["/start"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
