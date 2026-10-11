"""Command-line interface for inspection and downloads."""

from __future__ import annotations

import argparse
import json
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

from localwebvideodownloader import __version__
from localwebvideodownloader.errors import AppError, FormatUnavailableError
from localwebvideodownloader.filenames import validate_output_name
from localwebvideodownloader.models import (
    CaptionTrack,
    DownloadRequest,
    InspectionResult,
    MediaItem,
    ProgressEvent,
)
from localwebvideodownloader.ytdlp_backend import YtDlpBackend, get_capabilities


def _duration(value: float | None) -> str:
    if value is None:
        return "unknown duration"
    seconds = max(0, int(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _bytes(value: float | int | None) -> str:
    if value is None or value < 0:
        return "?"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return "?"


def _print_item(item: MediaItem, number: int) -> None:
    flags = []
    if item.is_live:
        flags.append("LIVE - unsupported")
    if item.has_drm:
        flags.append("DRM - unsupported")
    suffix = f" ({', '.join(flags)})" if flags else ""
    print(f"\n[{number}] {item.title}{suffix}")
    print(
        f"    ID: {item.media_id} | Extractor: {item.extractor or 'generic'} "
        f"| Duration: {_duration(item.duration_seconds)}"
    )
    print("    Formats:")
    print("      ID                 Resolution   Type            Ext    FPS    Bitrate   Codecs")
    for media_format in item.formats:
        bitrate = (
            f"{media_format.bitrate_kbps:.0f}k" if media_format.bitrate_kbps is not None else "-"
        )
        fps = f"{media_format.fps:g}" if media_format.fps is not None else "-"
        drm = " [DRM]" if media_format.has_drm else ""
        print(
            f"      {media_format.format_id[:18]:<18} "
            f"{media_format.resolution[:12]:<12} "
            f"{media_format.kind[:15]:<15} "
            f"{(media_format.extension or '-')[:6]:<6} "
            f"{fps[:6]:<6} {bitrate[:9]:<9} "
            f"{media_format.codec_summary}{drm}"
        )
    if item.captions:
        print("    Captions:")
        for track in item.captions:
            print(f"      {track.label}")
    else:
        print("    Captions: none advertised")


def print_inspection(result: InspectionResult) -> None:
    print(f"Found {len(result.items)} video item(s).")
    for number, item in enumerate(result.items, start=1):
        _print_item(item, number)


def _choose_item(result: InspectionResult, number: int) -> MediaItem:
    if number < 1 or number > len(result.items):
        raise FormatUnavailableError(
            f"Media item {number} does not exist; choose 1 through {len(result.items)}."
        )
    return result.items[number - 1]


def _choose_captions(
    item: MediaItem, requested: Sequence[str], include_automatic: bool
) -> tuple[CaptionTrack, ...]:
    if not requested:
        return ()
    wanted = {value.casefold() for value in requested}
    choose_all = "all" in wanted
    eligible = tuple(
        track
        for track in item.captions
        if (choose_all or track.language.casefold() in wanted)
        and (include_automatic or not track.automatic)
    )
    extension_order = {"vtt": 0, "srt": 1, "ass": 2, "ttml": 3}
    selected_by_language: dict[str, CaptionTrack] = {}
    for track in sorted(
        eligible,
        key=lambda candidate: (
            candidate.automatic,
            extension_order.get((candidate.extension or "").casefold(), 99),
            candidate.extension or "",
        ),
    ):
        selected_by_language.setdefault(track.language.casefold(), track)
    selected = tuple(selected_by_language.values())
    found = {track.language.casefold() for track in selected}
    missing = sorted(wanted - found - {"all"})
    if missing:
        extra = " Use --automatic-subtitles if only an automatic track is available."
        raise FormatUnavailableError(
            f"Caption language(s) not available: {', '.join(missing)}.{extra}"
        )
    if not selected:
        raise FormatUnavailableError("No matching caption tracks are available.")
    return selected


class _ConsoleProgress:
    def __init__(self) -> None:
        self._last_width = 0

    def __call__(self, event: ProgressEvent) -> None:
        if event.phase == "downloading":
            if event.fraction is None:
                amount = _bytes(event.downloaded_bytes)
            else:
                amount = f"{event.fraction * 100:5.1f}%"
            speed = (
                f" at {_bytes(event.speed_bytes_per_second)}/s"
                if event.speed_bytes_per_second
                else ""
            )
            eta = f" ETA {int(event.eta_seconds)}s" if event.eta_seconds is not None else ""
            message = f"Downloading {amount}{speed}{eta}"
            padding = " " * max(0, self._last_width - len(message))
            print(f"\r{message}{padding}", end="", flush=True)
            self._last_width = len(message)
            return
        if self._last_width:
            print()
            self._last_width = 0
        if event.message:
            print(event.message)


def _add_common_error_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="show a redacted diagnostic when an operation fails",
    )
    parser.add_argument(
        "--allow-private-network",
        action="store_true",
        help="allow trusted local/private URLs (disabled by default for safety)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="local-web-video-downloader",
        description="Inspect and download non-DRM embedded web video.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="list detected video metadata")
    inspect_parser.add_argument("url", help="public page, player, manifest, or media URL")
    inspect_parser.add_argument("--json", action="store_true", help="write JSON metadata")
    _add_common_error_option(inspect_parser)

    download_parser = subparsers.add_parser("download", help="download one detected video")
    download_parser.add_argument("url", help="public page, player, manifest, or media URL")
    download_parser.add_argument(
        "--media",
        type=int,
        default=1,
        metavar="NUMBER",
        help="1-based media item number when a page contains multiple videos (default: 1)",
    )
    download_parser.add_argument(
        "--format",
        default="best",
        metavar="ID",
        help="exact video format ID or 'best' (default: best)",
    )
    download_parser.add_argument(
        "--audio-format", metavar="ID", help="optional exact audio-only format ID"
    )
    download_parser.add_argument(
        "--subtitle",
        action="append",
        default=[],
        metavar="LANG",
        help="caption language to save; repeat it or use 'all'",
    )
    download_parser.add_argument(
        "--automatic-subtitles",
        action="store_true",
        help="use an automatic caption when no manual variant matches --subtitle",
    )
    download_parser.add_argument(
        "--embed-subtitles",
        action="store_true",
        help="embed selected captions (requires FFmpeg)",
    )
    download_parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "VideosDownloaded",
        metavar="DIRECTORY",
        help="output directory (default: VideosDownloaded in the application folder)",
    )
    download_parser.add_argument(
        "--output-name",
        metavar="NAME",
        help="optional file name without an extension; captions use the same name",
    )
    download_parser.add_argument(
        "--no-validate",
        action="store_true",
        help="do not validate the completed file with ffprobe",
    )
    download_parser.add_argument(
        "--list",
        action="store_true",
        help="inspect and list formats without downloading",
    )
    _add_common_error_option(download_parser)

    subparsers.add_parser("doctor", help="check required and optional local tools")
    subparsers.add_parser("gui", help="launch the desktop interface")
    return parser


def _run_inspect(args: argparse.Namespace, backend: YtDlpBackend) -> int:
    result = backend.inspect(args.url)
    if args.json:
        print(json.dumps(result.to_public_dict(), indent=2, ensure_ascii=False))
    else:
        print_inspection(result)
    return 0


def _run_download(args: argparse.Namespace, backend: YtDlpBackend) -> int:
    output_name = validate_output_name(args.output_name)
    result = backend.inspect(args.url)
    if args.list:
        print_inspection(result)
        return 0
    item = _choose_item(result, args.media)
    captions = _choose_captions(item, args.subtitle, args.automatic_subtitles)
    if args.embed_subtitles and not captions:
        raise FormatUnavailableError(
            "Choose at least one --subtitle language before using --embed-subtitles."
        )
    request = DownloadRequest(
        item=item,
        output_directory=args.output,
        format_id=args.format,
        audio_format_id=args.audio_format,
        captions=captions,
        embed_captions=args.embed_subtitles,
        validate_output=not args.no_validate,
        output_name=output_name,
    )
    print(f"Downloading: {item.title}")
    output = backend.download(
        request,
        progress=_ConsoleProgress(),
        cancel_event=threading.Event(),
    )
    if output.files:
        print("Output files:")
        for path in output.files:
            suffix = " (already existed; not overwritten)" if path in output.existing_files else ""
            print(f"  {path}{suffix}")
    else:
        print("Download completed; yt-dlp did not report a newly created filename.")
    if output.validated is True:
        print("Validation: ffprobe found the expected media stream.")
    elif output.validated is None:
        print("Validation: skipped (ffprobe unavailable or no new media file was reported).")
    return 0


def _run_doctor() -> int:
    capabilities = get_capabilities()
    try:
        import tkinter  # noqa: PLC0415

        tkinter_status = f"available (Tk {tkinter.TkVersion:g})"
    except ImportError:
        tkinter_status = "missing"
    print(f"Python: {sys.version.split()[0]}")
    print(
        "yt-dlp: "
        + (
            f"available ({capabilities.yt_dlp_version})"
            if capabilities.yt_dlp_available
            else "missing"
        )
    )
    print(f"FFmpeg: {capabilities.ffmpeg_path or 'missing'}")
    print(f"ffprobe: {capabilities.ffprobe_path or 'missing'}")
    print(f"Tkinter: {tkinter_status}")
    if not capabilities.yt_dlp_available:
        print("Install the project dependencies using the setup commands in README.md.")
    if not capabilities.ffmpeg_path or not capabilities.ffprobe_path:
        print("Install FFmpeg to enable merging, caption embedding, and validation.")
    return 0 if capabilities.yt_dlp_available else 3


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _run_doctor()
    if args.command == "gui":
        from localwebvideodownloader.gui import main as gui_main

        return gui_main()

    backend = YtDlpBackend(allow_private_network=getattr(args, "allow_private_network", False))
    try:
        if args.command == "inspect":
            return _run_inspect(args, backend)
        if args.command == "download":
            return _run_download(args, backend)
        parser.error("unknown command")
    except KeyboardInterrupt:
        print("\nCancelled. A resumable .part file may remain.", file=sys.stderr)
        return 130
    except AppError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        if getattr(args, "verbose", False) and exc.diagnostic:
            print(f"Diagnostic: {exc.diagnostic}", file=sys.stderr)
        return exc.exit_code
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
