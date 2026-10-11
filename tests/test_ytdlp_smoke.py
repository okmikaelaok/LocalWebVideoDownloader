from __future__ import annotations

import importlib.util
import sys
import tempfile
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from localwebvideodownloader.models import DownloadRequest
from localwebvideodownloader.ytdlp_backend import YtDlpBackend


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


@unittest.skipUnless(importlib.util.find_spec("yt_dlp"), "yt-dlp is not installed")
class RealYtDlpSmokeTests(unittest.TestCase):
    def test_static_iframe_is_inspected_and_direct_file_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            web_root = root / "web"
            output = root / "output"
            web_root.mkdir()
            (web_root / "outer.html").write_text(
                '<!doctype html><title>Outer</title><iframe src="player.html"></iframe>',
                encoding="utf-8",
            )
            (web_root / "player.html").write_text(
                """<!doctype html>
                <title>Fixture player</title>
                <video controls>
                  <source src="sample.mp4" type="video/mp4">
                  <track kind="subtitles" src="captions.vtt" srclang="en" label="English">
                </video>
                """,
                encoding="utf-8",
            )
            (web_root / "captions.vtt").write_text(
                "WEBVTT\n\n00:00.000 --> 00:01.000\nFixture caption\n",
                encoding="utf-8",
            )
            fixture_bytes = b"disposable direct-media fixture"
            (web_root / "sample.mp4").write_bytes(fixture_bytes)
            (web_root / "master.m3u8").write_text(
                """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",LANGUAGE="en",DEFAULT=YES,AUTOSELECT=YES,URI="subtitle.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=600000,RESOLUTION=640x360,CODECS="avc1.42e01e,mp4a.40.2",SUBTITLES="subs"
low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1800000,RESOLUTION=1280x720,CODECS="avc1.4d401f,mp4a.40.2",SUBTITLES="subs"
high.m3u8
""",
                encoding="utf-8",
            )
            media_playlist = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:1
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:1.0,
segment.ts
#EXT-X-ENDLIST
"""
            (web_root / "low.m3u8").write_text(media_playlist, encoding="utf-8")
            (web_root / "high.m3u8").write_text(media_playlist, encoding="utf-8")
            segment_bytes = b"disposable hls segment fixture"
            (web_root / "segment.ts").write_bytes(segment_bytes)
            (web_root / "subtitle.m3u8").write_text(
                """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:1
#EXTINF:1.0,
captions.vtt
#EXT-X-ENDLIST
""",
                encoding="utf-8",
            )
            (web_root / "manifest.mpd").write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static"
     mediaPresentationDuration="PT1S" minBufferTime="PT1S">
  <Period duration="PT1S">
    <AdaptationSet contentType="video" mimeType="video/mp4" segmentAlignment="true">
      <Representation id="dash-360" bandwidth="600000" width="640"
                      height="360" codecs="avc1.42E01E">
        <BaseURL>video-360.mp4</BaseURL>
      </Representation>
      <Representation id="dash-720" bandwidth="1800000" width="1280"
                      height="720" codecs="avc1.4D401F">
        <BaseURL>video-720.mp4</BaseURL>
      </Representation>
    </AdaptationSet>
    <AdaptationSet contentType="audio" mimeType="audio/mp4" lang="en">
      <Representation id="dash-audio" bandwidth="128000"
                      audioSamplingRate="48000" codecs="mp4a.40.2">
        <BaseURL>audio.m4a</BaseURL>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
""",
                encoding="utf-8",
            )

            handler = partial(_QuietHandler, directory=str(web_root))
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                page_url = f"http://127.0.0.1:{server.server_port}/outer.html"
                backend = YtDlpBackend(allow_private_network=True)
                result = backend.inspect(page_url)
                self.assertEqual(
                    len(result.items),
                    1,
                    [(item.key, item.media_id, item.content_fingerprint) for item in result.items],
                )
                item = next(candidate for candidate in result.items if candidate.video_formats)
                self.assertTrue(any(track.language == "en" for track in item.captions))
                english_caption = next(track for track in item.captions if track.language == "en")
                downloaded = backend.download(
                    DownloadRequest(
                        item=item,
                        output_directory=output,
                        captions=(english_caption,),
                        validate_output=False,
                    )
                )
                self.assertTrue(downloaded.files)
                media_file = next(path for path in downloaded.files if path.suffix == ".mp4")
                caption_file = next(path for path in downloaded.files if path.suffix == ".vtt")
                self.assertEqual(media_file.read_bytes(), fixture_bytes)
                self.assertIn("Fixture caption", caption_file.read_text(encoding="utf-8"))

                custom_name = "My video 100% %(title)s"
                custom_request = DownloadRequest(
                    item=item,
                    output_directory=root / "renamed-output",
                    output_name=custom_name,
                    captions=(english_caption,),
                    validate_output=False,
                )
                renamed = backend.download(custom_request)
                renamed_video = next(path for path in renamed.files if path.suffix == ".mp4")
                renamed_caption = next(path for path in renamed.files if path.suffix == ".vtt")
                self.assertEqual(renamed_video.name, f"{custom_name}.mp4")
                self.assertEqual(renamed_caption.name, f"{custom_name}.en.vtt")
                self.assertEqual(renamed_video.read_bytes(), fixture_bytes)
                # The same explicit name never silently overwrites an existing download.
                renamed_video.write_bytes(b"existing media retained")
                renamed_caption.write_text("existing captions retained", encoding="utf-8")
                repeated = backend.download(custom_request)
                self.assertIn(renamed_video, repeated.existing_files)
                self.assertIn(renamed_caption, repeated.existing_files)
                self.assertEqual(renamed_video.read_bytes(), b"existing media retained")
                self.assertEqual(
                    renamed_caption.read_text(encoding="utf-8"), "existing captions retained"
                )

                hls = backend.inspect(f"http://127.0.0.1:{server.server_port}/master.m3u8")
                hls_heights = {fmt.height for fmt in hls.items[0].video_formats}
                self.assertTrue({360, 720}.issubset(hls_heights))
                self.assertTrue(any(track.language == "en" for track in hls.items[0].captions))
                hls_format = min(
                    hls.items[0].video_formats,
                    key=lambda media_format: media_format.height or 0,
                )
                hls_caption = next(
                    track for track in hls.items[0].captions if track.language == "en"
                )
                hls_download = backend.download(
                    DownloadRequest(
                        item=hls.items[0],
                        output_directory=root / "hls-output",
                        format_id=hls_format.format_id,
                        captions=(hls_caption,),
                        validate_output=False,
                    )
                )
                self.assertTrue(
                    any(path.read_bytes() == segment_bytes for path in hls_download.files)
                )
                self.assertTrue(
                    any(
                        path.suffix == ".vtt"
                        and "Fixture caption" in path.read_text(encoding="utf-8")
                        for path in hls_download.files
                    )
                )

                dash = backend.inspect(f"http://127.0.0.1:{server.server_port}/manifest.mpd")
                dash_heights = {fmt.height for fmt in dash.items[0].video_formats}
                self.assertTrue({360, 720}.issubset(dash_heights))
                self.assertTrue(dash.items[0].audio_formats)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
