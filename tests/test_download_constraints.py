from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from localwebvideodownloader.errors import (
    FormatUnavailableError,
    LiveStreamError,
    ProtectedMediaError,
)
from localwebvideodownloader.ytdlp_backend import (
    _restricted_youtube_dl_class,
    _validate_hls_manifest,
)


class _BaseYoutubeDL:
    processed = False

    def __init__(self, params: dict[str, object]) -> None:
        self.params = params

    def process_info(self, info: object) -> str:
        _BaseYoutubeDL.processed = True
        return "processed"

    def process_subtitles(
        self, _video_id: str, _normal_subtitles: object, _automatic_captions: object
    ) -> dict[str, object]:
        return {"fallback": {}}


class DownloadConstraintTests(unittest.TestCase):
    def setUp(self) -> None:
        _BaseYoutubeDL.processed = False
        module = SimpleNamespace(YoutubeDL=_BaseYoutubeDL)
        self.downloader_class = _restricted_youtube_dl_class(module, allow_private_network=True)

    def test_external_transport_is_rejected_before_base_downloader(self) -> None:
        downloader = self.downloader_class({})
        with self.assertRaises(FormatUnavailableError):
            downloader.process_info(
                {
                    "protocol": "rtmp_ffmpeg",
                    "url": "rtmp://127.0.0.1/private",
                }
            )
        self.assertFalse(_BaseYoutubeDL.processed)

    def test_native_http_transport_reaches_base_downloader(self) -> None:
        downloader = self.downloader_class({})
        result = downloader.process_info(
            {
                "protocol": "https",
                "url": "https://127.0.0.1/video.mp4",
            }
        )
        self.assertEqual(result, "processed")
        self.assertTrue(_BaseYoutubeDL.processed)

    def test_external_caption_transport_is_rejected(self) -> None:
        downloader = self.downloader_class({})
        with self.assertRaises(FormatUnavailableError):
            downloader.process_info(
                {
                    "protocol": "https",
                    "url": "https://127.0.0.1/video.mp4",
                    "requested_subtitles": {
                        "en": {
                            "protocol": "rtmp_ffmpeg",
                            "url": "rtmp://127.0.0.1/captions",
                        }
                    },
                }
            )
        self.assertFalse(_BaseYoutubeDL.processed)

    def test_partial_section_cannot_trigger_external_downloader(self) -> None:
        downloader = self.downloader_class({})
        with self.assertRaises(FormatUnavailableError):
            downloader.process_info(
                {
                    "protocol": "https",
                    "url": "https://127.0.0.1/video.mp4",
                    "section_start": 5,
                }
            )
        self.assertFalse(_BaseYoutubeDL.processed)

    def test_caption_format_and_manual_or_automatic_type_are_exact(self) -> None:
        downloader = self.downloader_class(
            {
                "_localwebvideodownloader_caption_tracks": [
                    ("en", "srt", True),
                    ("fr", "vtt", False),
                ]
            }
        )
        selected = downloader.process_subtitles(
            "video",
            {
                "en": [{"ext": "vtt", "url": "https://example.test/manual-en"}],
                "fr": [{"ext": "vtt", "url": "https://example.test/manual-fr"}],
            },
            {
                "en": [
                    {"ext": "vtt", "url": "https://example.test/auto-en-vtt"},
                    {"ext": "srt", "url": "https://example.test/auto-en-srt"},
                ]
            },
        )
        self.assertEqual(selected["en"]["url"], "https://example.test/auto-en-srt")
        self.assertEqual(selected["fr"]["url"], "https://example.test/manual-fr")


class _FakeHlsDownloader:
    @classmethod
    def can_download(
        cls, _manifest: str, _info: object, allow_unplayable_formats: bool = False
    ) -> bool:
        return not allow_unplayable_formats

    @staticmethod
    def _has_drm(_manifest: str) -> bool:
        return False


class HlsManifestConstraintTests(unittest.TestCase):
    def test_finite_unencrypted_media_playlist_is_allowed(self) -> None:
        _validate_hls_manifest(
            "#EXTM3U\n#EXTINF:1,\nsegment.ts\n#EXT-X-ENDLIST\n",
            {"is_live": False},
            _FakeHlsDownloader,
        )

    def test_unfinished_playlist_is_rejected_as_live(self) -> None:
        with self.assertRaises(LiveStreamError):
            _validate_hls_manifest(
                "#EXTM3U\n#EXTINF:1,\nsegment.ts\n",
                {"is_live": False},
                _FakeHlsDownloader,
            )

    def test_encrypted_playlist_is_rejected(self) -> None:
        for indentation in ("", " ", "\t", "\ufeff"):
            with (
                self.subTest(indentation=repr(indentation)),
                self.assertRaises(ProtectedMediaError),
            ):
                _validate_hls_manifest(
                    f'#EXTM3U\n{indentation}#EXT-X-KEY:METHOD=AES-128,URI="key.bin"'
                    "\n#EXT-X-ENDLIST\n",
                    {"is_live": False},
                    _FakeHlsDownloader,
                )

    def test_key_uri_cannot_smuggle_method_none(self) -> None:
        with self.assertRaises(ProtectedMediaError):
            _validate_hls_manifest(
                '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key?x=METHOD=NONE"\n#EXT-X-ENDLIST\n',
                {"is_live": False},
                _FakeHlsDownloader,
            )


if __name__ == "__main__":
    unittest.main()
