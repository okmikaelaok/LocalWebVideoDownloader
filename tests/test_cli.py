from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from localwebvideodownloader.cli import _choose_captions, _run_download, build_parser
from localwebvideodownloader.errors import DownloadError, FormatUnavailableError
from localwebvideodownloader.models import CaptionTrack, DownloadResult, InspectionResult, MediaItem


class CaptionChoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.item = MediaItem(
            key="captions",
            title="Caption choices",
            media_id="one",
            source_url="https://example.test/video",
            captions=(
                CaptionTrack(language="en", extension="srt", automatic=False),
                CaptionTrack(language="en", extension="vtt", automatic=False),
                CaptionTrack(language="en", extension="vtt", automatic=True),
                CaptionTrack(language="fr", extension="vtt", automatic=True),
            ),
        )

    def test_selects_one_preferred_manual_variant_per_language(self) -> None:
        selected = _choose_captions(self.item, ["en"], include_automatic=True)
        self.assertEqual(selected, (CaptionTrack(language="en", extension="vtt"),))

    def test_automatic_track_is_an_opt_in_fallback(self) -> None:
        with self.assertRaises(FormatUnavailableError):
            _choose_captions(self.item, ["fr"], include_automatic=False)
        selected = _choose_captions(self.item, ["fr"], include_automatic=True)
        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0].automatic)

    def test_all_still_selects_only_one_track_per_language(self) -> None:
        selected = _choose_captions(self.item, ["all"], include_automatic=True)
        self.assertEqual({track.language for track in selected}, {"en", "fr"})
        self.assertEqual(len(selected), 2)


class OutputNameCliTests(unittest.TestCase):
    def test_optional_name_reaches_download_request(self) -> None:
        for name in (None, "My video 100%"):
            arguments = ["download", "https://example.test/video", "--no-validate"]
            if name is not None:
                arguments.extend(["--output-name", name])
            args = build_parser().parse_args(arguments)
            item = MediaItem(key="one", title="Video", media_id="one", source_url=args.url)
            backend = Mock()
            backend.inspect.return_value = InspectionResult(requested_url=args.url, items=(item,))
            backend.download.return_value = DownloadResult(files=(), validated=None)
            with self.subTest(name=name), patch("builtins.print"):
                self.assertEqual(_run_download(args, backend), 0)
            self.assertEqual(backend.download.call_args.args[0].output_name, name)

    def test_invalid_name_does_not_start_inspection(self) -> None:
        args = build_parser().parse_args(
            ["download", "https://example.test/video", "--output-name", "../outside"]
        )
        backend = Mock()
        with self.assertRaises(DownloadError):
            _run_download(args, backend)
        backend.inspect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
