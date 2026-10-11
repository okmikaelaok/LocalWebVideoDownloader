from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from localwebvideodownloader.errors import DownloadError
from localwebvideodownloader.filenames import (
    MAX_OUTPUT_NAME_BYTES,
    output_templates,
    suggest_output_name,
    validate_output_name,
)


class OutputNameTests(unittest.TestCase):
    def test_blank_names_preserve_existing_templates(self) -> None:
        expected = {
            "default": "%(title).140B [%(id).64B].%(ext)s",
            "subtitle": "%(title).120B [%(id).64B].%(language).32B.%(ext)s",
        }
        for value in (None, "", "   "):
            with self.subTest(value=value):
                self.assertIsNone(validate_output_name(value))
                self.assertEqual(output_templates(value), expected)

    def test_literal_names_allow_unicode_dots_and_percent_signs(self) -> None:
        for value in ("My video", "Episode 1.2", "100% %(title)s", "Café 東京", "photo [1]"):
            with self.subTest(value=value):
                self.assertEqual(validate_output_name(value), value)

    def test_paths_and_platform_invalid_names_are_rejected(self) -> None:
        invalid = (
            ".",
            "..",
            "../outside",
            "..\\outside",
            "/root/video",
            "C:\\video",
            "C:video",
            "\\\\server\\video",
            "video/clip",
            "video\\clip",
            "video:stream",
            "a?b",
            "a*b",
            'a"b',
            "<video>",
            "a|b",
            "video.",
            "video ",
            " video",
            "line\nfeed",
            "tab\there",
            "null\x00byte",
            "delete\x7f",
            "control\u0085",
            "bidi\u202e",
            "\ud800",
            "\t",
        )
        for value in invalid:
            with self.subTest(value=repr(value)), self.assertRaises(DownloadError):
                validate_output_name(value)

    def test_windows_devices_are_rejected_on_every_platform(self) -> None:
        for value in (
            "CON",
            "con.mp4",
            "PrN",
            "AUX.txt",
            "NUL",
            "COM1",
            "COM9",
            "lpt1",
            "LPT9.srt",
            "COM¹",
            "LPT²",
            "COM³",
            "CONIN$",
            "CONOUT$.mp4",
            "CON .mp4",
        ):
            with self.subTest(value=value), self.assertRaises(DownloadError):
                validate_output_name(value)
        self.assertEqual(validate_output_name("COM10"), "COM10")
        self.assertEqual(validate_output_name("console"), "console")

    def test_length_budget_is_bytes_and_suggestions_remain_valid(self) -> None:
        self.assertEqual(validate_output_name("a" * MAX_OUTPUT_NAME_BYTES), "a" * 160)
        for value in ("a" * 161, "é" * 81):
            with self.subTest(value=value), self.assertRaises(DownloadError):
                validate_output_name(value)
        for title in ("a" * 300, "🎞" * 100, "?CON/clip:", "..", "", "NUL", "a\x00b", "\ud800"):
            with self.subTest(title=repr(title)):
                suggestion = suggest_output_name(title)
                self.assertLessEqual(len(suggestion.encode("utf-8")), MAX_OUTPUT_NAME_BYTES)
                self.assertEqual(validate_output_name(suggestion), suggestion)

    def test_template_metacharacters_are_literal_and_captions_share_basename(self) -> None:
        self.assertEqual(
            output_templates("100% %(title)s"),
            {
                "default": "100%% %%(title)s.%(ext)s",
                "subtitle": "100%% %%(title)s.%(ext)s",
            },
        )

    @unittest.skipUnless(importlib.util.find_spec("yt_dlp"), "yt-dlp is not installed")
    def test_real_ytdlp_renders_template_characters_without_expansion(self) -> None:
        import yt_dlp

        for value in ("100% %(title)s", "%%(id)s", "%s", "literal %(ext)s"):
            with (
                self.subTest(value=value),
                yt_dlp.YoutubeDL(
                    {"outtmpl": output_templates(value), "quiet": True, "windowsfilenames": True}
                ) as downloader,
            ):
                self.assertEqual(
                    downloader.prepare_filename({"title": "UNEXPECTED", "id": "42", "ext": "mp4"}),
                    f"{value}.mp4",
                )


if __name__ == "__main__":
    unittest.main()
