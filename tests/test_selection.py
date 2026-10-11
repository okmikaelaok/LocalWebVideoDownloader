from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from localwebvideodownloader.errors import FormatUnavailableError, ProtectedMediaError
from localwebvideodownloader.models import MediaFormat, MediaItem
from localwebvideodownloader.selection import SAFE_PROTOCOL_FILTER, build_format_selector


def safe(format_id: str) -> str:
    return f"{format_id}{SAFE_PROTOCOL_FILTER}"


def make_item(*formats: MediaFormat, drm: bool = False) -> MediaItem:
    return MediaItem(
        key="test:key",
        title="Test video",
        media_id="video-1",
        source_url="https://example.test/watch/1?token=secret",
        formats=formats,
        has_drm=drm,
    )


class FormatSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.video_only = MediaFormat(format_id="137", video_codec="avc1", audio_codec="none")
        self.audio_only = MediaFormat(format_id="140", video_codec="none", audio_codec="aac")
        self.muxed = MediaFormat(format_id="22", video_codec="avc1", audio_codec="aac")
        self.item = make_item(self.video_only, self.audio_only, self.muxed)

    def test_best_selector_uses_video_and_audio_fallback(self) -> None:
        self.assertEqual(
            build_format_selector(self.item),
            f"{safe('bestvideo*')}+{safe('bestaudio')}/{safe('best')}",
        )

    def test_best_with_explicit_audio_is_strict(self) -> None:
        self.assertEqual(
            build_format_selector(self.item, "best", "140"),
            f"{safe('bestvideo')}+{safe('140')}",
        )
        with self.assertRaises(FormatUnavailableError):
            build_format_selector(self.item, "best", "22")

    def test_video_only_automatically_adds_best_audio(self) -> None:
        self.assertEqual(
            build_format_selector(self.item, "137"),
            f"{safe('137')}+{safe('bestaudio')}",
        )

    def test_video_only_without_advertised_audio_stays_video_only(self) -> None:
        self.assertEqual(build_format_selector(make_item(self.video_only), "137"), safe("137"))

    def test_exact_video_and_audio_are_combined(self) -> None:
        self.assertEqual(
            build_format_selector(self.item, "137", "140"),
            f"{safe('137')}+{safe('140')}",
        )

    def test_muxed_format_is_used_without_extra_audio(self) -> None:
        self.assertEqual(build_format_selector(self.item, "22"), safe("22"))

    def test_separate_audio_is_rejected_for_muxed_format(self) -> None:
        with self.assertRaises(FormatUnavailableError):
            build_format_selector(self.item, "22", "140")

    def test_unknown_or_expression_format_is_rejected(self) -> None:
        for format_id in ("missing", "137/best", "137+bestaudio"):
            with self.subTest(format_id=format_id), self.assertRaises(FormatUnavailableError):
                build_format_selector(self.item, format_id)

    def test_drm_item_and_format_are_rejected(self) -> None:
        with self.assertRaises(ProtectedMediaError):
            build_format_selector(make_item(self.video_only, drm=True))
        protected = MediaFormat(format_id="drm", video_codec="avc1", has_drm=True)
        with self.assertRaises(ProtectedMediaError):
            build_format_selector(make_item(protected), "drm")


if __name__ == "__main__":
    unittest.main()
