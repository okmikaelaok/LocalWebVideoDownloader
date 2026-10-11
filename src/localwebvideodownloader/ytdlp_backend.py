"""yt-dlp adapter for metadata inspection and downloading."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from localwebvideodownloader.errors import (
    AccessDeniedError,
    AppError,
    DependencyError,
    DownloadCancelled,
    DownloadError,
    FormatUnavailableError,
    InspectionError,
    InvalidUrlError,
    LiveStreamError,
    NetworkError,
    NoMediaError,
    ProtectedMediaError,
    ValidationError,
)
from localwebvideodownloader.filenames import output_templates
from localwebvideodownloader.html_discovery import discover_embedded_urls
from localwebvideodownloader.models import (
    CaptionTrack,
    DownloadRequest,
    DownloadResult,
    InspectionResult,
    MediaFormat,
    MediaItem,
    ProgressEvent,
)
from localwebvideodownloader.security import (
    redact_sensitive_text,
    referrer_for_target,
    validate_network_url,
)
from localwebvideodownloader.selection import SAFE_MEDIA_PROTOCOLS, build_format_selector

ProgressCallback = Callable[[ProgressEvent], None]
MAX_HLS_MANIFEST_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    yt_dlp_available: bool
    yt_dlp_version: str | None
    ffmpeg_path: str | None
    ffprobe_path: str | None


class _SafeLogger:
    """A silent yt-dlp logger that retains only a redacted final error."""

    def __init__(self) -> None:
        self.last_error: str | None = None

    def debug(self, _message: object) -> None:
        return

    def info(self, _message: object) -> None:
        return

    def warning(self, _message: object) -> None:
        return

    def error(self, message: object) -> None:
        self.last_error = redact_sensitive_text(message)


def _load_yt_dlp() -> Any:
    try:
        import yt_dlp  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise DependencyError(
            "yt-dlp is not installed. Run the setup commands in README.md first."
        ) from exc
    return yt_dlp


def _restricted_youtube_dl_class(yt_dlp: Any, *, allow_private_network: bool) -> Any:
    """Return a YoutubeDL class with constrained captions and transports.

    yt-dlp owns redirects, manifests, and segment requests after extraction begins,
    so validating only the user-entered URL is insufficient. The restricted class
    forces yt-dlp's built-in urllib handler and validates initial and redirected
    request URLs unless private-network access is explicitly enabled. Unsupported
    external-downloader protocols are rejected in both modes.
    """

    redirect_handler_type: Any = None
    if not allow_private_network:
        try:
            from yt_dlp.networking._urllib import (  # type: ignore[import-not-found]
                RedirectHandler,
            )
        except ImportError as exc:
            raise DependencyError(
                "The installed yt-dlp version does not expose the required safe network handler."
            ) from exc
        redirect_handler_type = RedirectHandler

    base_class = yt_dlp.YoutubeDL

    class RestrictedYoutubeDL(base_class):  # type: ignore[misc, valid-type]
        def process_subtitles(
            self,
            video_id: str,
            normal_subtitles: object,
            automatic_captions: object,
        ) -> Any:
            requested_tracks = self.params.get("_localwebvideodownloader_caption_tracks")
            if not requested_tracks:
                return super().process_subtitles(video_id, normal_subtitles, automatic_captions)

            selected: dict[str, Mapping[str, Any]] = {}
            for language, extension, automatic in requested_tracks:
                source = automatic_captions if automatic else normal_subtitles
                variants = source.get(language) if isinstance(source, Mapping) else None
                if not isinstance(variants, list) or not variants:
                    kind = "automatic" if automatic else "manual"
                    raise FormatUnavailableError(
                        f"The selected {kind} caption for {language} is no longer available."
                    )
                matching = [
                    variant
                    for variant in variants
                    if isinstance(variant, Mapping)
                    and (
                        not extension
                        or (_as_text(variant.get("ext")) or "").casefold() == extension.casefold()
                    )
                ]
                if not matching:
                    raise FormatUnavailableError(
                        f"The selected {language} caption format is no longer available."
                    )
                selected[language] = dict(matching[-1])
            return selected

        def process_info(self, info_dict: Mapping[str, Any]) -> Any:
            _validate_download_transport(info_dict, allow_private_network=allow_private_network)
            return super().process_info(info_dict)

        def dl(
            self,
            name: str,
            info: Mapping[str, Any],
            subtitle: bool = False,
            test: bool = False,
        ) -> Any:
            from yt_dlp.downloader import (  # type: ignore[import-not-found]
                DashSegmentsFD,
                HlsFD,
                HttpFD,
                get_suitable_downloader,
            )

            safe_info = dict(info)
            _validate_download_transport(safe_info, allow_private_network=allow_private_network)
            downloader_type = get_suitable_downloader(safe_info, self.params, to_stdout=name == "-")
            if downloader_type not in {HttpFD, HlsFD, DashSegmentsFD}:
                raise FormatUnavailableError(
                    "The selected media requires an external downloader and is not supported."
                )
            if downloader_type is HlsFD:
                manifest = safe_info.get("hls_media_playlist_data")
                if isinstance(manifest, bytes):
                    manifest = manifest.decode("utf-8", "replace")
                if not isinstance(manifest, str):
                    hls_downloader = HlsFD(self, self.params)
                    response = self.urlopen(
                        hls_downloader._prepare_url(safe_info, str(safe_info["url"]))
                    )
                    try:
                        payload = response.read(MAX_HLS_MANIFEST_BYTES + 1)
                        final_url = str(response.url)
                    finally:
                        response.close()
                    if len(payload) > MAX_HLS_MANIFEST_BYTES:
                        raise FormatUnavailableError(
                            "The HLS playlist is too large to process safely."
                        )
                    manifest = payload.decode("utf-8", "replace")
                    validate_network_url(final_url, allow_private_network=allow_private_network)
                    safe_info["url"] = final_url
                _validate_hls_manifest(manifest, safe_info, HlsFD)
                safe_info["hls_media_playlist_data"] = manifest
            return super().dl(name, safe_info, subtitle=subtitle, test=test)

        def urlopen(self, request: object) -> Any:
            if allow_private_network:
                return super().urlopen(request)
            if isinstance(request, str):
                request_url = request
            elif hasattr(request, "url"):
                request_url = str(request.url)
            elif hasattr(request, "get_full_url"):
                request_url = str(request.get_full_url())
            else:
                raise InvalidUrlError("A downloader request did not contain a valid URL.")
            validate_network_url(request_url, allow_private_network=False)
            return super().urlopen(request)

        def build_request_director(self, handlers: object, preferences: object = None) -> Any:
            director = super().build_request_director(handlers, preferences)
            if allow_private_network:
                return director
            urllib_handler = director.handlers.get("Urllib")
            if urllib_handler is None:
                raise DependencyError(
                    "yt-dlp's safe urllib request handler is unavailable in this environment."
                )

            # Optional request backends may implement redirects differently. Force
            # the handler whose redirect path we validate below.
            director.handlers = {"Urllib": urllib_handler}
            original_create_instance = urllib_handler._create_instance

            def create_checked_opener(*args: object, **kwargs: object) -> Any:
                opener = original_create_instance(*args, **kwargs)
                for handler in opener.handlers:
                    if not isinstance(handler, redirect_handler_type):
                        continue
                    original_redirect = handler.redirect_request

                    def checked_redirect(
                        req: object,
                        fp: object,
                        code: int,
                        msg: str,
                        headers: object,
                        newurl: str,
                        *,
                        _redirect: Any = original_redirect,
                    ) -> Any:
                        absolute_url = urljoin(str(req.full_url), newurl)
                        validate_network_url(absolute_url, allow_private_network=False)
                        return _redirect(req, fp, code, msg, headers, absolute_url)

                    handler.redirect_request = checked_redirect
                return opener

            urllib_handler._create_instance = create_checked_opener
            return director

    return RestrictedYoutubeDL


def get_capabilities() -> BackendCapabilities:
    try:
        yt_dlp = _load_yt_dlp()
        try:
            version = str(yt_dlp.version.__version__)
        except (AttributeError, TypeError):
            version = "installed"
        available = True
    except DependencyError:
        version = None
        available = False
    return BackendCapabilities(
        yt_dlp_available=available,
        yt_dlp_version=version,
        ffmpeg_path=shutil.which("ffmpeg"),
        ffprobe_path=shutil.which("ffprobe"),
    )


def _as_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _as_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _as_text(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _protocol_is_supported(protocol: str | None) -> bool:
    return bool(protocol) and all(
        part in SAFE_MEDIA_PROTOCOLS for part in protocol.casefold().split("+")
    )


def _format_transport_is_supported(raw: Mapping[str, Any]) -> bool:
    protocol = _as_text(raw.get("protocol"))
    if protocol and not _protocol_is_supported(protocol):
        return False
    media_url = _as_text(raw.get("url"))
    return not media_url or urlsplit(media_url).scheme.casefold() in {"http", "https"}


def _validate_download_transport(info: Mapping[str, Any], *, allow_private_network: bool) -> None:
    """Reject external transports before yt-dlp can launch a downloader."""

    live_status = (_as_text(info.get("live_status")) or "").casefold()
    if info.get("is_live") or live_status in {"is_live", "is_upcoming", "post_live"}:
        raise LiveStreamError("Live streams are not supported by the initial version.")
    if info.get("has_drm"):
        raise ProtectedMediaError("DRM-protected media is not supported.")
    if info.get("section_start") is not None or info.get("section_end") is not None:
        raise FormatUnavailableError("Partial-section downloads are not supported.")

    def validate_entry(entry: Mapping[str, Any], *, description: str) -> None:
        if entry.get("has_drm"):
            raise ProtectedMediaError(f"The selected {description} is DRM-protected.")
        media_url = _as_text(entry.get("url"))
        protocol = _as_text(entry.get("protocol"))
        if not protocol and media_url:
            scheme = urlsplit(media_url).scheme.casefold()
            protocol = scheme if scheme in {"http", "https"} else None
        if not _protocol_is_supported(protocol):
            raise FormatUnavailableError(
                f"The selected {description} uses an unsupported network protocol. "
                "Only HTTP(S), HLS, and DASH downloads are allowed."
            )
        if not media_url:
            raise FormatUnavailableError(f"The selected {description} has no downloadable URL.")
        validate_network_url(media_url, allow_private_network=allow_private_network)

    requested_formats = info.get("requested_formats")
    if isinstance(requested_formats, list) and requested_formats:
        formats = [item for item in requested_formats if isinstance(item, Mapping)]
    else:
        formats = [info]

    if not formats:
        raise FormatUnavailableError("The selected media format is no longer available.")
    for media_format in formats:
        validate_entry(media_format, description="media")

    requested_subtitles = info.get("requested_subtitles")
    if isinstance(requested_subtitles, Mapping):
        for subtitle in requested_subtitles.values():
            if isinstance(subtitle, Mapping):
                validate_entry(subtitle, description="caption")


def _validate_hls_manifest(manifest: str, info: Mapping[str, Any], hls_type: Any) -> None:
    """Ensure native HLS cannot fall back to an external network downloader."""

    lines = [line.strip().upper() for line in manifest.splitlines()]
    if any("#EXT-X-STREAM-INF" in line for line in lines):
        raise FormatUnavailableError("Select a concrete HLS quality rather than a master playlist.")
    if "#EXT-X-ENDLIST" not in lines:
        raise LiveStreamError("Live or unfinished HLS playlists are not supported.")
    for line in lines:
        if "#EXT-X-KEY" in line or "#EXT-X-SESSION-KEY" in line:
            raise ProtectedMediaError("Encrypted HLS playlists are not supported.")
    if not hls_type.can_download(manifest, info, allow_unplayable_formats=False):
        if hls_type._has_drm(manifest):
            raise ProtectedMediaError("DRM-protected HLS playlists are not supported.")
        raise FormatUnavailableError(
            "This HLS playlist requires an external downloader and is not supported."
        )


def _iter_leaf_entries(info: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    entries = info.get("entries")
    if isinstance(entries, (list, tuple)):
        for entry in entries:
            if isinstance(entry, Mapping):
                yield from _iter_leaf_entries(entry)
        return
    yield info


def _normalize_formats(info: Mapping[str, Any]) -> tuple[MediaFormat, ...]:
    raw_formats = info.get("formats")
    if not isinstance(raw_formats, list):
        raw_formats = [info] if info.get("url") else []

    formats: list[MediaFormat] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_formats):
        if not isinstance(raw, Mapping):
            continue
        if not _format_transport_is_supported(raw):
            continue
        format_id = _as_text(raw.get("format_id")) or f"source-{index + 1}"
        if format_id in seen:
            continue
        video_codec = _as_text(raw.get("vcodec"))
        audio_codec = _as_text(raw.get("acodec"))
        if video_codec == "none" and audio_codec == "none":
            continue
        if not video_codec and not audio_codec:
            extension = (_as_text(raw.get("ext")) or "").lower()
            if extension in {"mp4", "webm", "mov", "mkv", "m4v"}:
                video_codec = "unknown"
                audio_codec = "unknown"
            elif extension in {"m4a", "mp3", "aac", "opus", "ogg", "wav"}:
                audio_codec = "unknown"
            else:
                continue

        filesize = _as_int(raw.get("filesize")) or _as_int(raw.get("filesize_approx"))
        formats.append(
            MediaFormat(
                format_id=format_id,
                extension=_as_text(raw.get("ext")),
                width=_as_int(raw.get("width")),
                height=_as_int(raw.get("height")),
                fps=_as_float(raw.get("fps")),
                video_codec=video_codec,
                audio_codec=audio_codec,
                bitrate_kbps=_as_float(raw.get("tbr")),
                filesize=filesize,
                protocol=_as_text(raw.get("protocol")),
                language=_as_text(raw.get("language")),
                note=_as_text(raw.get("format_note")) or _as_text(raw.get("format")),
                has_drm=bool(raw.get("has_drm")),
            )
        )
        seen.add(format_id)

    def sort_key(item: MediaFormat) -> tuple[int, int, float, str]:
        category = 0 if item.has_video else 1 if item.has_audio else 2
        return (
            category,
            -(item.height or 0),
            -(item.bitrate_kbps or 0),
            item.format_id,
        )

    return tuple(sorted(formats, key=sort_key))


def _normalize_captions(info: Mapping[str, Any]) -> tuple[CaptionTrack, ...]:
    tracks: list[CaptionTrack] = []
    seen: set[tuple[str, str | None, bool]] = set()
    for field, automatic in (("subtitles", False), ("automatic_captions", True)):
        raw_languages = info.get(field)
        if not isinstance(raw_languages, Mapping):
            continue
        for language, variants in raw_languages.items():
            language_text = _as_text(language)
            if not language_text or not isinstance(variants, list):
                continue
            for variant in variants:
                if not isinstance(variant, Mapping):
                    continue
                extension = _as_text(variant.get("ext"))
                identity = (language_text, extension, automatic)
                if identity in seen:
                    continue
                tracks.append(
                    CaptionTrack(
                        language=language_text,
                        name=_as_text(variant.get("name")),
                        extension=extension,
                        automatic=automatic,
                    )
                )
                seen.add(identity)
    return tuple(
        sorted(tracks, key=lambda item: (item.language, item.automatic, item.extension or ""))
    )


def normalize_info_tree(info: Mapping[str, Any], requested_url: str) -> tuple[MediaItem, ...]:
    """Convert a yt-dlp result tree into stable, deduplicated media items."""

    items: list[MediaItem] = []
    seen: set[str] = set()
    for position, entry in enumerate(_iter_leaf_entries(info), start=1):
        formats = _normalize_formats(entry)
        if not formats or not any(media_format.has_video for media_format in formats):
            continue

        media_id = _as_text(entry.get("id")) or f"item-{position}"
        extractor = _as_text(entry.get("extractor_key")) or _as_text(entry.get("extractor"))
        webpage_url = _as_text(entry.get("webpage_url"))
        original_url = _as_text(entry.get("original_url"))
        direct_url = _as_text(entry.get("url"))
        source_url = webpage_url or original_url or direct_url or requested_url
        # Reuse the original page with a playlist index only when an entry did not
        # expose its own distinct page URL.
        playlist_index = _as_int(entry.get("playlist_index"))
        if source_url != requested_url:
            playlist_index = None

        raw_formats = entry.get("formats")
        format_urls: list[str] = []
        if isinstance(raw_formats, list):
            format_urls.extend(
                value
                for raw in raw_formats
                if isinstance(raw, Mapping)
                if (value := _as_text(raw.get("url"))) is not None
            )
        if direct_url:
            format_urls.append(direct_url)
        unique_format_urls = sorted(set(format_urls))
        content_fingerprint = (
            hashlib.sha256("\0".join(unique_format_urls).encode("utf-8", "replace")).hexdigest()[
                :16
            ]
            if unique_format_urls
            else None
        )
        identity_source = f"{extractor or ''}\0{media_id}\0{source_url}"
        identity = hashlib.sha256(identity_source.encode("utf-8", "replace")).hexdigest()[:12]
        if identity in seen:
            continue
        seen.add(identity)

        playable_formats = [item for item in formats if item.has_video or item.has_audio]
        all_protected = bool(playable_formats) and all(item.has_drm for item in playable_formats)
        live_status = (_as_text(entry.get("live_status")) or "").lower()
        items.append(
            MediaItem(
                key=f"{extractor or 'media'}:{media_id}:{identity}",
                title=_as_text(entry.get("title")) or f"Video {position}",
                media_id=media_id,
                source_url=source_url,
                extractor=extractor,
                duration_seconds=_as_float(entry.get("duration")),
                formats=formats,
                captions=_normalize_captions(entry),
                is_live=bool(entry.get("is_live"))
                or live_status in {"is_live", "is_upcoming", "post_live"},
                has_drm=bool(entry.get("is_drm")) or all_protected,
                playlist_index=playlist_index,
                content_fingerprint=content_fingerprint,
            )
        )
    return tuple(items)


def progress_from_hook(data: Mapping[str, Any]) -> ProgressEvent:
    status = _as_text(data.get("status")) or "downloading"
    total = _as_int(data.get("total_bytes")) or _as_int(data.get("total_bytes_estimate"))
    filename = _as_text(data.get("filename"))
    return ProgressEvent(
        phase=status,
        downloaded_bytes=_as_int(data.get("downloaded_bytes")),
        total_bytes=total,
        speed_bytes_per_second=_as_float(data.get("speed")),
        eta_seconds=_as_float(data.get("eta")),
        filename=Path(filename).name if filename else None,
    )


def _classify_backend_error(error: object, *, downloading: bool) -> AppError:
    raw = str(error)
    lowered = raw.lower()
    diagnostic = redact_sensitive_text(raw)
    if "drm" in lowered or "encrypted" in lowered or "content protection" in lowered:
        return ProtectedMediaError(
            "This source appears to be protected or encrypted and is not supported.",
            diagnostic=diagnostic,
        )
    if "requested format is not available" in lowered or "format is not available" in lowered:
        return FormatUnavailableError(
            "The selected format is no longer available. Inspect the page again.",
            diagnostic=diagnostic,
        )
    if "ffmpeg" in lowered and ("not found" in lowered or "not installed" in lowered):
        return DependencyError(
            "FFmpeg is required for this operation but was not found on PATH.",
            diagnostic=diagnostic,
        )
    if any(
        marker in lowered
        for marker in ("http error 401", "http error 403", "unauthorized", "forbidden", "sign in")
    ):
        return AccessDeniedError(
            "The server denied access. Signed-in or private videos are outside the initial scope.",
            diagnostic=diagnostic,
        )
    if any(marker in lowered for marker in ("timed out", "timeout", "connection refused", "dns")):
        return NetworkError("The page or media server could not be reached.", diagnostic=diagnostic)
    if any(
        marker in lowered
        for marker in (
            "unsupported url",
            "no video formats found",
            "no media found",
            "does not contain any video",
        )
    ):
        return NoMediaError(
            "No supported video was found at this URL. The player may require a browser session.",
            diagnostic=diagnostic,
        )
    if downloading:
        return DownloadError("The download failed.", diagnostic=diagnostic)
    return InspectionError("The page could not be inspected.", diagnostic=diagnostic)


def _prefer_inspection_error(current: AppError | None, candidate: AppError) -> AppError:
    priorities: tuple[type[AppError], ...] = (
        ProtectedMediaError,
        AccessDeniedError,
        NetworkError,
        DependencyError,
        InspectionError,
        NoMediaError,
    )

    def priority(error: AppError) -> int:
        for index, error_type in enumerate(priorities):
            if isinstance(error, error_type):
                return len(priorities) - index
        return 0

    if current is None or priority(candidate) > priority(current):
        return candidate
    return current


def _emit(callback: ProgressCallback | None, event: ProgressEvent) -> None:
    if callback is not None:
        callback(event)


def _snapshot_files(directory: Path) -> set[Path]:
    try:
        return {path.resolve() for path in directory.iterdir() if path.is_file()}
    except OSError:
        return set()


def _collect_result_paths(info: object) -> set[Path]:
    """Collect filenames reported by yt-dlp, including already existing files."""

    if not isinstance(info, Mapping):
        return set()
    paths: set[Path] = set()
    for field in ("filepath", "_filename", "filename"):
        value = _as_text(info.get(field))
        if value:
            paths.add(Path(value))
    for field in ("entries", "requested_downloads"):
        children = info.get(field)
        if isinstance(children, (list, tuple)):
            for child in children:
                paths.update(_collect_result_paths(child))
    requested_subtitles = info.get("requested_subtitles")
    if isinstance(requested_subtitles, Mapping):
        for subtitle in requested_subtitles.values():
            paths.update(_collect_result_paths(subtitle))
    return paths


def _find_media_tool(name: str, ffmpeg_location: str | None = None) -> str | None:
    if ffmpeg_location:
        location = Path(ffmpeg_location).expanduser()
        candidates: list[Path] = []
        if location.is_dir():
            candidates.extend((location / name, location / f"{name}.exe"))
        else:
            candidates.extend((location.with_name(name), location.with_name(f"{name}.exe")))
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    return shutil.which(name)


def _within_directory(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except (OSError, ValueError):
        return False


_CAPTION_EXTENSIONS = {
    ".ass",
    ".dfxp",
    ".json3",
    ".lrc",
    ".srt",
    ".ssa",
    ".srv1",
    ".srv2",
    ".srv3",
    ".ttml",
    ".vtt",
    ".xml",
}


def _caption_languages_found(files: set[Path], languages: list[str]) -> set[str]:
    found: set[str] = set()
    for path in files:
        if path.suffix.casefold() not in _CAPTION_EXTENSIONS:
            continue
        lowered_name = path.name.casefold()
        for language in languages:
            if f".{language.casefold()}." in lowered_name:
                found.add(language)
    return found


class YtDlpBackend:
    """Inspect and download media through a constrained yt-dlp configuration."""

    def __init__(
        self,
        *,
        ffmpeg_location: str | None = None,
        allow_private_network: bool = False,
        referer_url: str | None = None,
    ) -> None:
        self.ffmpeg_location = ffmpeg_location
        self.allow_private_network = allow_private_network
        self.referer_url = (
            validate_network_url(referer_url, allow_private_network=self.allow_private_network)
            if referer_url
            else None
        )

    def _request_headers_for(self, target_url: str) -> dict[str, str]:
        if not self.referer_url:
            return {}
        referer = referrer_for_target(
            self.referer_url,
            target_url,
            allow_private_network=self.allow_private_network,
        )
        return {"Referer": referer} if referer else {}

    def inspect(self, url: str) -> InspectionResult:
        requested_url = validate_network_url(url, allow_private_network=self.allow_private_network)
        yt_dlp = _load_yt_dlp()
        youtube_dl_class = _restricted_youtube_dl_class(
            yt_dlp, allow_private_network=self.allow_private_network
        )
        logger = _SafeLogger()
        options: dict[str, Any] = {
            "skip_download": True,
            "extract_flat": False,
            "ignoreconfig": True,
            "quiet": True,
            "no_warnings": True,
            "logger": logger,
            "socket_timeout": 20,
            "retries": 2,
            "fragment_retries": 2,
            "playlistend": 25,
            "cachedir": False,
        }
        try:
            embedded_urls = discover_embedded_urls(
                requested_url, allow_private_network=self.allow_private_network
            )
        except AppError:
            raise
        except Exception:
            # Static iframe discovery is an enhancement. A malformed response must
            # not prevent yt-dlp from inspecting the URL directly.
            embedded_urls = ()
        candidate_urls = (requested_url, *embedded_urls)
        items: list[MediaItem] = []
        first_error: AppError | None = None
        for candidate_url in candidate_urls:
            candidate_url = validate_network_url(
                candidate_url, allow_private_network=self.allow_private_network
            )
            candidate_options = dict(options)
            request_headers = self._request_headers_for(candidate_url)
            if request_headers:
                candidate_options["http_headers"] = request_headers
            try:
                with youtube_dl_class(candidate_options) as downloader:
                    raw_info = downloader.extract_info(candidate_url, download=False)
            except AppError as exc:
                first_error = _prefer_inspection_error(first_error, exc)
                continue
            except Exception as exc:
                first_error = _prefer_inspection_error(
                    first_error, _classify_backend_error(exc, downloading=False)
                )
                continue
            if isinstance(raw_info, Mapping):
                items.extend(normalize_info_tree(raw_info, candidate_url))

        if not items:
            if first_error is not None and not isinstance(first_error, NoMediaError):
                raise first_error
            raise NoMediaError(
                "No supported video was found at this URL. "
                "The player may load media only in a browser.",
                diagnostic=(
                    first_error.diagnostic
                    if isinstance(first_error, NoMediaError) and first_error.diagnostic
                    else logger.last_error
                ),
            )

        unique_items: list[MediaItem] = []
        seen: set[tuple[str, str]] = set()
        for item in items:
            extractor = (item.extractor or "generic").casefold()
            if item.content_fingerprint:
                identity = ("content", item.content_fingerprint)
            elif extractor == "generic":
                identity = (extractor, item.key)
            else:
                identity = (extractor, item.media_id)
            if identity in seen:
                continue
            seen.add(identity)
            unique_items.append(item)
        return InspectionResult(requested_url=requested_url, items=tuple(unique_items))

    def download(
        self,
        request: DownloadRequest,
        *,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> DownloadResult:
        templates = output_templates(request.output_name)
        if request.item.is_live:
            raise LiveStreamError("Live streams are outside the initial version's scope.")
        source_url = validate_network_url(
            request.item.source_url, allow_private_network=self.allow_private_network
        )
        selector = build_format_selector(request.item, request.format_id, request.audio_format_id)
        yt_dlp = _load_yt_dlp()
        youtube_dl_class = _restricted_youtube_dl_class(
            yt_dlp, allow_private_network=self.allow_private_network
        )

        if request.embed_captions:
            if not _find_media_tool("ffmpeg", self.ffmpeg_location):
                raise DependencyError("FFmpeg is required to embed captions but was not found.")
            if not _find_media_tool("ffprobe", self.ffmpeg_location):
                raise DependencyError(
                    "ffprobe is required to verify embedded captions but was not found."
                )

        output_directory = request.output_directory.expanduser().resolve()
        try:
            output_directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DownloadError(
                "The output directory could not be created.",
                diagnostic=redact_sensitive_text(exc),
            ) from exc
        if not output_directory.is_dir():
            raise DownloadError("The selected output path is not a directory.")

        logger = _SafeLogger()
        observed_paths: set[Path] = set()
        before = _snapshot_files(output_directory)

        def check_cancelled() -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled()

        def progress_hook(data: Mapping[str, Any]) -> None:
            check_cancelled()
            raw_filename = _as_text(data.get("filename"))
            if raw_filename:
                observed_paths.add(Path(raw_filename))
            _emit(progress, progress_from_hook(data))

        def postprocessor_hook(data: Mapping[str, Any]) -> None:
            check_cancelled()
            info = data.get("info_dict")
            if isinstance(info, Mapping):
                filepath = _as_text(info.get("filepath")) or _as_text(info.get("_filename"))
                if filepath:
                    observed_paths.add(Path(filepath))
            _emit(
                progress,
                ProgressEvent(
                    phase="processing",
                    message=_as_text(data.get("postprocessor")) or "Processing media",
                ),
            )

        selected_languages = list(dict.fromkeys(track.language for track in request.captions))
        folded_languages = [language.casefold() for language in selected_languages]
        if len(set(folded_languages)) != len(request.captions):
            raise FormatUnavailableError(
                "Choose only one caption format or type for each language."
            )
        selected_extensions = list(
            dict.fromkeys(track.extension for track in request.captions if track.extension)
        )
        options: dict[str, Any] = {
            "format": selector,
            "paths": {"home": str(output_directory), "temp": str(output_directory)},
            "outtmpl": templates,
            "windowsfilenames": os.name == "nt",
            "overwrites": False,
            "continuedl": True,
            "nopart": False,
            "noplaylist": request.item.playlist_index is None,
            "ignoreconfig": True,
            "quiet": True,
            "no_warnings": True,
            "logger": logger,
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            "concurrent_fragment_downloads": 3,
            "hls_prefer_native": True,
            "external_downloader": "native",
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [postprocessor_hook],
            "writesubtitles": any(not track.automatic for track in request.captions),
            "writeautomaticsub": any(track.automatic for track in request.captions),
            "subtitleslangs": selected_languages,
            "subtitlesformat": "/".join((*selected_extensions, "best")),
            "_localwebvideodownloader_caption_tracks": [
                (track.language, track.extension, track.automatic) for track in request.captions
            ],
            "cachedir": False,
        }
        if request.item.playlist_index is not None:
            options["playlist_items"] = str(request.item.playlist_index)
        if self.ffmpeg_location:
            options["ffmpeg_location"] = self.ffmpeg_location
        request_headers = self._request_headers_for(source_url)
        if request_headers:
            options["http_headers"] = request_headers
        if request.embed_captions and selected_languages:
            options["postprocessors"] = [
                {"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False}
            ]

        check_cancelled()
        _emit(progress, ProgressEvent(phase="starting", message="Starting download"))
        try:
            with youtube_dl_class(options) as downloader:
                downloaded_info = downloader.extract_info(source_url, download=True)
        except AppError:
            raise
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled() from exc
            raise _classify_backend_error(exc, downloading=True) from exc

        reported_paths = observed_paths | _collect_result_paths(downloaded_info)
        observed_paths.update(reported_paths)
        after = _snapshot_files(output_directory)
        candidates = (after - before) | {path.resolve() for path in observed_paths if path.exists()}
        files = tuple(
            sorted(
                (
                    path
                    for path in candidates
                    if path.is_file()
                    and not path.name.endswith((".part", ".ytdl"))
                    and _within_directory(path, output_directory)
                ),
                key=lambda path: path.name.lower(),
            )
        )
        if request.captions:
            caption_evidence = reported_paths if request.embed_captions else set(files)
            found_languages = _caption_languages_found(caption_evidence, selected_languages)
            missing_languages = [
                language for language in selected_languages if language not in found_languages
            ]
            if missing_languages:
                raise ValidationError(
                    "The video downloaded, but the selected caption language(s) were not saved: "
                    + ", ".join(missing_languages)
                )
        must_validate = request.validate_output or request.embed_captions
        validated = self._validate_outputs(files, request) if must_validate else None
        _emit(progress, ProgressEvent(phase="complete", message="Download complete"))
        existing_files = tuple(path for path in files if path in before)
        return DownloadResult(files=files, validated=validated, existing_files=existing_files)

    def _validate_outputs(self, files: tuple[Path, ...], request: DownloadRequest) -> bool | None:
        ffprobe = _find_media_tool("ffprobe", self.ffmpeg_location)
        if not ffprobe:
            return None
        media_extensions = {
            ".mp4",
            ".mkv",
            ".webm",
            ".mov",
            ".m4v",
            ".avi",
            ".ts",
            ".m2ts",
            ".mp3",
            ".m4a",
            ".aac",
            ".opus",
            ".ogg",
            ".wav",
            ".flac",
        }
        media_files = [path for path in files if path.suffix.lower() in media_extensions]
        if not media_files:
            if request.embed_captions:
                raise ValidationError("Caption embedding finished without a media file to verify.")
            return None

        selected = (
            request.item.find_format(request.format_id) if request.format_id != "best" else None
        )
        required_stream_types: set[str]
        if selected and selected.has_audio and not selected.has_video:
            required_stream_types = {"audio"}
        else:
            required_stream_types = {"video"}
        item_has_audio = any(media_format.has_audio for media_format in request.item.formats)
        if request.audio_format_id:
            required_stream_types.add("audio")
        elif selected is not None:
            if selected.has_audio or (selected.has_video and request.item.audio_formats):
                required_stream_types.add("audio")
        elif request.format_id == "best" and item_has_audio:
            required_stream_types.add("audio")
        for media_file in media_files:
            try:
                result = subprocess.run(
                    [
                        ffprobe,
                        "-v",
                        "error",
                        "-show_entries",
                        "stream=codec_type:stream_tags=language",
                        "-of",
                        "json",
                        str(media_file),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValidationError(
                    "The downloaded file could not be validated with ffprobe.",
                    diagnostic=redact_sensitive_text(exc),
                ) from exc
            if result.returncode != 0:
                continue
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError:
                continue
            streams = [
                stream for stream in payload.get("streams", []) if isinstance(stream, Mapping)
            ]
            stream_types = {stream.get("codec_type") for stream in streams}
            subtitle_streams = [
                stream for stream in streams if stream.get("codec_type") == "subtitle"
            ]
            enough_captions = not request.embed_captions or len(subtitle_streams) >= len(
                {track.language.casefold() for track in request.captions}
            )
            if required_stream_types.issubset(stream_types) and enough_captions:
                return True
        expected = " and ".join(
            f"a {stream_type} stream" for stream_type in sorted(required_stream_types)
        )
        if request.embed_captions:
            expected += " and an embedded subtitle stream"
        raise ValidationError(f"The download finished, but ffprobe did not find {expected}.")
