"""Translate user selections into constrained yt-dlp format expressions."""

from __future__ import annotations

import re

from localwebvideodownloader.errors import FormatUnavailableError, ProtectedMediaError
from localwebvideodownloader.models import MediaItem

_SAFE_FORMAT_ID = re.compile(r"^[A-Za-z0-9._:-]+$")
SAFE_MEDIA_PROTOCOLS = frozenset(
    {
        "http",
        "https",
        "m3u8",
        "m3u8_native",
        "http_dash_segments",
        "http_dash_segments_generator",
    }
)
SAFE_PROTOCOL_FILTER = (
    "[protocol~='^(?:https?|m3u8(?:_native)?|http_dash_segments(?:_generator)?)$']"
)


def _safe_format(format_id: str) -> str:
    return f"{format_id}{SAFE_PROTOCOL_FILTER}"


def _validated_format_id(value: str) -> str:
    if not value or not _SAFE_FORMAT_ID.fullmatch(value):
        raise FormatUnavailableError("The selected format identifier is invalid.")
    return value


def build_format_selector(
    item: MediaItem,
    format_id: str = "best",
    audio_format_id: str | None = None,
) -> str:
    """Build a format expression without accepting arbitrary yt-dlp syntax."""

    if item.has_drm:
        raise ProtectedMediaError(
            "This video appears to be DRM-protected and cannot be downloaded."
        )

    if format_id == "best":
        if audio_format_id:
            audio_id = _validated_format_id(audio_format_id)
            audio = item.find_format(audio_id)
            if audio is None or not audio.has_audio:
                raise FormatUnavailableError("The selected audio format is no longer available.")
            if audio.has_video:
                raise FormatUnavailableError("Select an audio-only format for the audio track.")
            if audio.has_drm:
                raise ProtectedMediaError("The selected audio format is DRM-protected.")
            return f"{_safe_format('bestvideo')}+{_safe_format(audio_id)}"
        return f"{_safe_format('bestvideo*')}+{_safe_format('bestaudio')}/{_safe_format('best')}"

    selected_id = _validated_format_id(format_id)
    selected = item.find_format(selected_id)
    if selected is None:
        raise FormatUnavailableError("The selected video format is no longer available.")
    if selected.has_drm:
        raise ProtectedMediaError("The selected video format is DRM-protected.")

    if audio_format_id:
        audio_id = _validated_format_id(audio_format_id)
        audio = item.find_format(audio_id)
        if audio is None or not audio.has_audio:
            raise FormatUnavailableError("The selected audio format is no longer available.")
        if audio.has_video:
            raise FormatUnavailableError("Select an audio-only format for the audio track.")
        if audio.has_drm:
            raise ProtectedMediaError("The selected audio format is DRM-protected.")
        if selected.has_audio:
            raise FormatUnavailableError(
                "The selected video already contains audio. Choose a video-only format "
                "before selecting a separate audio track."
            )
        return f"{_safe_format(selected_id)}+{_safe_format(audio_id)}"

    if selected.has_video and not selected.has_audio:
        return (
            f"{_safe_format(selected_id)}+{_safe_format('bestaudio')}"
            if item.audio_formats
            else _safe_format(selected_id)
        )
    return _safe_format(selected_id)
