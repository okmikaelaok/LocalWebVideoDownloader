"""Stable application models independent of yt-dlp metadata dictionaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from localwebvideodownloader.security import safe_display_url


def _codec_present(codec: str | None) -> bool:
    return bool(codec and codec.lower() != "none")


def _format_bytes(value: int | None) -> str:
    if not value or value <= 0:
        return "unknown size"
    size = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return "unknown size"


@dataclass(frozen=True, slots=True)
class MediaFormat:
    format_id: str
    extension: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    bitrate_kbps: float | None = None
    filesize: int | None = None
    protocol: str | None = None
    language: str | None = None
    note: str | None = None
    has_drm: bool = False

    @property
    def has_video(self) -> bool:
        return _codec_present(self.video_codec)

    @property
    def has_audio(self) -> bool:
        return _codec_present(self.audio_codec)

    @property
    def kind(self) -> str:
        if self.has_video and self.has_audio:
            return "video + audio"
        if self.has_video:
            return "video only"
        if self.has_audio:
            return "audio only"
        return "unknown"

    @property
    def resolution(self) -> str:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        if self.height:
            return f"{self.height}p"
        return self.note or "unknown"

    @property
    def codec_summary(self) -> str:
        codecs = [codec for codec in (self.video_codec, self.audio_codec) if _codec_present(codec)]
        return " / ".join(codecs) if codecs else "unknown"

    @property
    def label(self) -> str:
        parts = [self.format_id, self.resolution, self.kind]
        if self.extension:
            parts.append(self.extension)
        if self.language:
            parts.append(self.language)
        if self.has_drm:
            parts.append("DRM")
        return " | ".join(parts)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "format_id": self.format_id,
            "extension": self.extension,
            "resolution": self.resolution,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "kind": self.kind,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "bitrate_kbps": self.bitrate_kbps,
            "filesize": self.filesize,
            "filesize_label": _format_bytes(self.filesize),
            "protocol": self.protocol,
            "language": self.language,
            "note": self.note,
            "has_drm": self.has_drm,
        }


@dataclass(frozen=True, slots=True)
class CaptionTrack:
    language: str
    name: str | None = None
    extension: str | None = None
    automatic: bool = False

    @property
    def label(self) -> str:
        parts = [self.language]
        if self.name and self.name.lower() != self.language.lower():
            parts.append(self.name)
        if self.extension:
            parts.append(self.extension)
        parts.append("automatic" if self.automatic else "manual")
        return " | ".join(parts)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "name": self.name,
            "extension": self.extension,
            "automatic": self.automatic,
        }


@dataclass(frozen=True, slots=True)
class MediaItem:
    key: str
    title: str
    media_id: str
    source_url: str = field(repr=False)
    extractor: str | None = None
    duration_seconds: float | None = None
    formats: tuple[MediaFormat, ...] = ()
    captions: tuple[CaptionTrack, ...] = ()
    is_live: bool = False
    has_drm: bool = False
    playlist_index: int | None = field(default=None, repr=False)
    content_fingerprint: str | None = field(default=None, repr=False)

    @property
    def video_formats(self) -> tuple[MediaFormat, ...]:
        return tuple(item for item in self.formats if item.has_video)

    @property
    def audio_formats(self) -> tuple[MediaFormat, ...]:
        return tuple(item for item in self.formats if item.has_audio and not item.has_video)

    def find_format(self, format_id: str) -> MediaFormat | None:
        return next((item for item in self.formats if item.format_id == format_id), None)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "media_id": self.media_id,
            "source": safe_display_url(self.source_url),
            "extractor": self.extractor,
            "duration_seconds": self.duration_seconds,
            "is_live": self.is_live,
            "has_drm": self.has_drm,
            "formats": [item.to_public_dict() for item in self.formats],
            "captions": [item.to_public_dict() for item in self.captions],
        }


@dataclass(frozen=True, slots=True)
class InspectionResult:
    requested_url: str = field(repr=False)
    items: tuple[MediaItem, ...] = ()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "requested_url": safe_display_url(self.requested_url),
            "item_count": len(self.items),
            "items": [item.to_public_dict() for item in self.items],
        }


@dataclass(frozen=True, slots=True)
class DownloadRequest:
    item: MediaItem
    output_directory: Path
    format_id: str = "best"
    audio_format_id: str | None = None
    captions: tuple[CaptionTrack, ...] = ()
    embed_captions: bool = False
    validate_output: bool = True
    output_name: str | None = None


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    phase: str
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    speed_bytes_per_second: float | None = None
    eta_seconds: float | None = None
    filename: str | None = None
    message: str | None = None

    @property
    def fraction(self) -> float | None:
        if not self.total_bytes or self.total_bytes <= 0 or self.downloaded_bytes is None:
            return None
        return max(0.0, min(1.0, self.downloaded_bytes / self.total_bytes))


@dataclass(frozen=True, slots=True)
class DownloadResult:
    files: tuple[Path, ...]
    validated: bool | None
    existing_files: tuple[Path, ...] = ()
