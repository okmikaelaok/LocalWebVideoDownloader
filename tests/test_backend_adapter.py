from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from localwebvideodownloader.errors import DownloadCancelled, DownloadError
from localwebvideodownloader.models import CaptionTrack, DownloadRequest, MediaFormat, MediaItem
from localwebvideodownloader.selection import SAFE_PROTOCOL_FILTER
from localwebvideodownloader.ytdlp_backend import YtDlpBackend

SAMPLE_INFO = {
    "id": "abc",
    "title": "Example",
    "webpage_url": "https://player.example.test/video/abc",
    "extractor_key": "TestPlayer",
    "formats": [
        {
            "format_id": "video-720",
            "ext": "mp4",
            "width": 1280,
            "height": 720,
            "vcodec": "avc1",
            "acodec": "none",
        },
        {
            "format_id": "audio-en",
            "ext": "m4a",
            "vcodec": "none",
            "acodec": "aac",
            "language": "en",
        },
    ],
    "subtitles": {"en": [{"ext": "vtt", "name": "English"}]},
}


class FakeYoutubeDL:
    option_history: list[dict[str, object]] = []
    create_captions = True

    def __init__(self, options: dict[str, object]) -> None:
        self.options = options
        self.option_history.append(options)

    def __enter__(self) -> FakeYoutubeDL:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def extract_info(self, _url: str, *, download: bool) -> dict[str, object]:
        if not download:
            return SAMPLE_INFO
        output = Path(str(self.options["paths"]["home"])) / "Example [abc].mp4"  # type: ignore[index]
        output.write_bytes(b"fake media")
        caption_paths: list[Path] = []
        if self.create_captions:
            for language in self.options.get("subtitleslangs", []):  # type: ignore[union-attr]
                caption_path = output.parent / f"Example [abc].{language}.vtt"
                caption_path.write_text("WEBVTT\n", encoding="utf-8")
                caption_paths.append(caption_path)
        for hook in self.options["progress_hooks"]:  # type: ignore[index,union-attr]
            for caption_path in caption_paths:
                hook({"status": "finished", "filename": str(caption_path)})
            hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 10,
                    "total_bytes": 10,
                    "filename": str(output),
                }
            )
            hook({"status": "finished", "filename": str(output)})
        for hook in self.options["postprocessor_hooks"]:  # type: ignore[index,union-attr]
            hook(
                {
                    "status": "finished",
                    "postprocessor": "Fake merger",
                    "info_dict": {"filepath": str(output)},
                }
            )
        return SAMPLE_INFO


class BackendAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeYoutubeDL.option_history.clear()
        FakeYoutubeDL.create_captions = True
        self.fake_module = SimpleNamespace(YoutubeDL=FakeYoutubeDL)

    def test_inspect_then_download_uses_constrained_options(self) -> None:
        backend = YtDlpBackend(allow_private_network=True)
        with (
            patch("localwebvideodownloader.ytdlp_backend._load_yt_dlp", return_value=self.fake_module),
            patch("localwebvideodownloader.ytdlp_backend.discover_embedded_urls", return_value=()),
            patch("localwebvideodownloader.ytdlp_backend.shutil.which", return_value=None),
            tempfile.TemporaryDirectory() as temporary_directory,
        ):
            inspection = backend.inspect("https://example.test/page?token=secret")
            item = inspection.items[0]
            events = []
            request = DownloadRequest(
                item=item,
                output_directory=Path(temporary_directory),
                format_id="video-720",
                audio_format_id="audio-en",
                captions=(CaptionTrack(language="en", extension="vtt"),),
            )
            result = backend.download(request, progress=events.append)

        self.assertEqual(len(result.files), 2)
        self.assertIn("Example [abc].mp4", {path.name for path in result.files})
        self.assertIn("Example [abc].en.vtt", {path.name for path in result.files})
        self.assertIsNone(result.validated)
        self.assertTrue(any(event.phase == "downloading" for event in events))
        download_options = FakeYoutubeDL.option_history[-1]
        self.assertEqual(
            download_options["format"],
            f"video-720{SAFE_PROTOCOL_FILTER}+audio-en{SAFE_PROTOCOL_FILTER}",
        )
        self.assertTrue(download_options["continuedl"])
        self.assertFalse(download_options["overwrites"])
        self.assertTrue(download_options["writesubtitles"])
        self.assertEqual(download_options["subtitleslangs"], ["en"])
        self.assertEqual(download_options["subtitlesformat"], "vtt/best")
        self.assertEqual(download_options["external_downloader"], "native")
        self.assertTrue(download_options["ignoreconfig"])
        self.assertEqual(
            download_options["outtmpl"],
            {
                "default": "%(title).140B [%(id).64B].%(ext)s",
                "subtitle": "%(title).120B [%(id).64B].%(language).32B.%(ext)s",
            },
        )

    def test_custom_name_is_escaped_without_changing_resume_or_overwrite_options(self) -> None:
        backend = YtDlpBackend(allow_private_network=True)
        with (
            patch("localwebvideodownloader.ytdlp_backend._load_yt_dlp", return_value=self.fake_module),
            patch("localwebvideodownloader.ytdlp_backend.discover_embedded_urls", return_value=()),
            tempfile.TemporaryDirectory() as directory,
        ):
            item = backend.inspect("https://example.test/page").items[0]
            backend.download(
                DownloadRequest(
                    item=item,
                    output_directory=Path(directory),
                    output_name="100% %(title)s",
                    validate_output=False,
                )
            )
        options = FakeYoutubeDL.option_history[-1]
        self.assertEqual(options["outtmpl"]["default"], "100%% %%(title)s.%(ext)s")
        self.assertEqual(options["outtmpl"]["subtitle"], "100%% %%(title)s.%(ext)s")
        self.assertTrue(options["continuedl"])
        self.assertFalse(options["overwrites"])

    def test_invalid_name_is_rejected_before_network_or_filesystem_writes(self) -> None:
        item = MediaItem(
            key="one", title="Video", media_id="one", source_url="https://example.test"
        )
        with (
            patch("localwebvideodownloader.ytdlp_backend.validate_network_url") as network,
            patch("localwebvideodownloader.ytdlp_backend.Path.mkdir") as mkdir,
            self.assertRaises(DownloadError),
        ):
            YtDlpBackend().download(
                DownloadRequest(item=item, output_directory=Path("unused"), output_name="../escape")
            )
        network.assert_not_called()
        mkdir.assert_not_called()

    def test_missing_requested_caption_is_reported_as_failure(self) -> None:
        backend = YtDlpBackend(allow_private_network=True)
        FakeYoutubeDL.create_captions = False
        with (
            patch("localwebvideodownloader.ytdlp_backend._load_yt_dlp", return_value=self.fake_module),
            patch("localwebvideodownloader.ytdlp_backend.discover_embedded_urls", return_value=()),
            tempfile.TemporaryDirectory() as temporary_directory,
        ):
            inspected = backend.inspect("https://example.test/page").items[0]
            request = DownloadRequest(
                item=inspected,
                output_directory=Path(temporary_directory),
                captions=(CaptionTrack(language="en", extension="vtt"),),
            )
            from localwebvideodownloader.errors import ValidationError

            with self.assertRaises(ValidationError):
                backend.download(request)

    def test_browser_capture_referrer_is_bounded_and_reused_for_download(self) -> None:
        backend = YtDlpBackend(
            allow_private_network=True,
            referer_url="https://player.example.test/watch/abc?session=private#position",
        )
        with (
            patch("localwebvideodownloader.ytdlp_backend._load_yt_dlp", return_value=self.fake_module),
            patch("localwebvideodownloader.ytdlp_backend.discover_embedded_urls", return_value=()),
            tempfile.TemporaryDirectory() as temporary_directory,
        ):
            inspected = backend.inspect("https://cdn.example.test/master.m3u8").items[0]
            inspection_headers = FakeYoutubeDL.option_history[-1]["http_headers"]
            backend.download(
                DownloadRequest(
                    item=inspected,
                    output_directory=Path(temporary_directory),
                )
            )
            download_headers = FakeYoutubeDL.option_history[-1]["http_headers"]

        self.assertEqual(inspection_headers, {"Referer": "https://player.example.test/"})
        self.assertEqual(download_headers, {"Referer": "https://player.example.test/"})

    def test_caption_embedding_adds_ffmpeg_postprocessor(self) -> None:
        backend = YtDlpBackend(allow_private_network=True)
        with (
            patch("localwebvideodownloader.ytdlp_backend._load_yt_dlp", return_value=self.fake_module),
            patch("localwebvideodownloader.ytdlp_backend.discover_embedded_urls", return_value=()),
            patch(
                "localwebvideodownloader.ytdlp_backend._find_media_tool",
                side_effect=lambda name, _location=None: f"C:/fake/{name}.exe",
            ),
            patch.object(YtDlpBackend, "_validate_outputs", return_value=True),
            tempfile.TemporaryDirectory() as temporary_directory,
        ):
            inspected = backend.inspect("https://example.test/page").items[0]
            request = DownloadRequest(
                item=inspected,
                output_directory=Path(temporary_directory),
                captions=(CaptionTrack(language="en", extension="vtt"),),
                embed_captions=True,
                validate_output=False,
            )
            backend.download(request)
        self.assertEqual(
            FakeYoutubeDL.option_history[-1]["postprocessors"],
            [{"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False}],
        )

    def test_validation_accepts_video_only_source_without_advertised_audio(self) -> None:
        backend = YtDlpBackend(allow_private_network=True)
        video_only = MediaFormat(format_id="137", video_codec="avc1", audio_codec="none")
        item = MediaItem(
            key="video-only",
            title="Silent video",
            media_id="silent",
            source_url="https://example.test/silent",
            formats=(video_only,),
        )
        with (
            patch("localwebvideodownloader.ytdlp_backend._find_media_tool", return_value="ffprobe"),
            patch(
                "localwebvideodownloader.ytdlp_backend.subprocess.run",
                return_value=SimpleNamespace(
                    returncode=0,
                    stdout='{"streams": [{"codec_type": "video"}]}',
                ),
            ),
            tempfile.TemporaryDirectory() as temporary_directory,
        ):
            output = Path(temporary_directory) / "silent.mp4"
            output.write_bytes(b"fake media")
            request = DownloadRequest(
                item=item,
                output_directory=Path(temporary_directory),
                format_id="137",
            )

            self.assertTrue(backend._validate_outputs((output,), request))

    def test_already_cancelled_request_never_starts_downloader(self) -> None:
        backend = YtDlpBackend(allow_private_network=True)
        with (
            patch("localwebvideodownloader.ytdlp_backend._load_yt_dlp", return_value=self.fake_module),
            patch("localwebvideodownloader.ytdlp_backend.discover_embedded_urls", return_value=()),
            tempfile.TemporaryDirectory() as temporary_directory,
        ):
            inspected = backend.inspect("https://example.test/page").items[0]
            event = threading.Event()
            event.set()
            request = DownloadRequest(item=inspected, output_directory=Path(temporary_directory))
            before = len(FakeYoutubeDL.option_history)
            with self.assertRaises(DownloadCancelled):
                backend.download(request, cancel_event=event)
            self.assertEqual(len(FakeYoutubeDL.option_history), before)

    def test_cancellation_during_progress_stops_before_completion(self) -> None:
        backend = YtDlpBackend(allow_private_network=True)
        with (
            patch("localwebvideodownloader.ytdlp_backend._load_yt_dlp", return_value=self.fake_module),
            patch("localwebvideodownloader.ytdlp_backend.discover_embedded_urls", return_value=()),
            tempfile.TemporaryDirectory() as temporary_directory,
        ):
            inspected = backend.inspect("https://example.test/page").items[0]
            event = threading.Event()
            phases: list[str] = []

            def cancel_after_progress(progress: object) -> None:
                phases.append(progress.phase)  # type: ignore[attr-defined]
                if progress.phase == "downloading":  # type: ignore[attr-defined]
                    event.set()

            request = DownloadRequest(item=inspected, output_directory=Path(temporary_directory))
            with self.assertRaises(DownloadCancelled):
                backend.download(request, progress=cancel_after_progress, cancel_event=event)
            self.assertIn("downloading", phases)
            self.assertNotIn("complete", phases)


if __name__ == "__main__":
    unittest.main()
