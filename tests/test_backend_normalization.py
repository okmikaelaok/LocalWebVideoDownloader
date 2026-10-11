from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from localwebvideodownloader.models import MediaFormat
from localwebvideodownloader.ytdlp_backend import normalize_info_tree, progress_from_hook


class MetadataNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.info = {
            "_type": "playlist",
            "entries": [
                {
                    "id": "abc123",
                    "title": "Example clip",
                    "webpage_url": "https://player.example.test/watch/abc123?token=secret",
                    "extractor_key": "ExamplePlayer",
                    "duration": 61.5,
                    "formats": [
                        {
                            "format_id": "audio-en",
                            "ext": "m4a",
                            "vcodec": "none",
                            "acodec": "aac",
                            "language": "en",
                            "tbr": 128,
                        },
                        {
                            "format_id": "720p",
                            "ext": "mp4",
                            "width": 1280,
                            "height": 720,
                            "fps": 30,
                            "vcodec": "avc1",
                            "acodec": "none",
                            "tbr": 2200,
                            "protocol": "m3u8_native",
                        },
                        {
                            "format_id": "360p",
                            "ext": "mp4",
                            "width": 640,
                            "height": 360,
                            "vcodec": "avc1",
                            "acodec": "aac",
                            "filesize_approx": 123456,
                        },
                    ],
                    "subtitles": {
                        "en": [
                            {"ext": "vtt", "name": "English"},
                            {"ext": "vtt", "name": "duplicate URL variant"},
                        ]
                    },
                    "automatic_captions": {"fr": [{"ext": "vtt", "name": "French"}]},
                },
                None,
            ],
        }

    def test_normalizes_formats_captions_and_safe_public_metadata(self) -> None:
        items = normalize_info_tree(self.info, "https://example.test/outer")
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.title, "Example clip")
        self.assertEqual(item.duration_seconds, 61.5)
        self.assertEqual([value.format_id for value in item.formats], ["720p", "360p", "audio-en"])
        self.assertEqual(item.formats[0].kind, "video only")
        self.assertEqual(item.formats[1].kind, "video + audio")
        self.assertEqual(len(item.captions), 2)
        self.assertFalse(item.captions[0].automatic)
        self.assertTrue(item.captions[1].automatic)
        public = item.to_public_dict()
        self.assertNotIn("token=secret", public["source"])
        self.assertEqual(public["source"], "https://player.example.test/<redacted-path>")

    def test_all_protected_formats_mark_item_as_drm(self) -> None:
        info = {
            "id": "protected",
            "url": "https://cdn.example.test/video",
            "formats": [
                {
                    "format_id": "drm",
                    "vcodec": "avc1",
                    "acodec": "none",
                    "has_drm": True,
                }
            ],
        }
        self.assertTrue(normalize_info_tree(info, "https://example.test/watch")[0].has_drm)

    def test_content_fingerprint_distinguishes_generic_items_with_same_id(self) -> None:
        first = {
            "id": "same-id",
            "extractor_key": "Generic",
            "webpage_url": "https://example.test/first",
            "formats": [
                {
                    "format_id": "0",
                    "url": "https://cdn.example.test/first.mp4",
                    "ext": "mp4",
                    "vcodec": "avc1",
                    "acodec": "aac",
                }
            ],
        }
        second = {
            **first,
            "webpage_url": "https://example.test/second",
            "formats": [
                {
                    **first["formats"][0],
                    "url": "https://cdn.example.test/second.mp4",
                }
            ],
        }
        first_item = normalize_info_tree(first, "https://example.test/page")[0]
        second_item = normalize_info_tree(second, "https://example.test/page")[0]
        self.assertNotEqual(first_item.content_fingerprint, second_item.content_fingerprint)

    def test_external_downloader_protocols_are_not_exposed(self) -> None:
        info = {
            "id": "mixed",
            "formats": [
                {
                    "format_id": "safe",
                    "url": "https://cdn.example.test/video.mp4",
                    "protocol": "https",
                    "vcodec": "avc1",
                    "acodec": "aac",
                },
                {
                    "format_id": "rtmp",
                    "url": "rtmp://127.0.0.1/private",
                    "protocol": "rtmp_ffmpeg",
                    "vcodec": "avc1",
                    "acodec": "aac",
                },
            ],
        }
        item = normalize_info_tree(info, "https://example.test/watch")[0]
        self.assertEqual([media_format.format_id for media_format in item.formats], ["safe"])

    def test_media_format_properties(self) -> None:
        media_format = MediaFormat(
            format_id="22",
            width=1280,
            height=720,
            video_codec="avc1",
            audio_codec="aac",
            extension="mp4",
        )
        self.assertEqual(media_format.resolution, "1280x720")
        self.assertEqual(media_format.kind, "video + audio")
        self.assertIn("mp4", media_format.label)


class ProgressNormalizationTests(unittest.TestCase):
    def test_progress_uses_estimate_and_clamps_fraction(self) -> None:
        event = progress_from_hook(
            {
                "status": "downloading",
                "downloaded_bytes": 125,
                "total_bytes_estimate": 100,
                "speed": 50.5,
                "eta": 2,
                "filename": r"C:\downloads\clip.part",
            }
        )
        self.assertEqual(event.phase, "downloading")
        self.assertEqual(event.fraction, 1.0)
        self.assertEqual(event.speed_bytes_per_second, 50.5)
        self.assertEqual(event.filename, "clip.part")


if __name__ == "__main__":
    unittest.main()
