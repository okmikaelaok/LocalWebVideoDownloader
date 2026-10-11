"""Validated messages shared by the browser extension and desktop launcher."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from localwebvideodownloader.errors import ValidationError
from localwebvideodownloader.security import validate_network_url

SCHEMA_VERSION = 1
NATIVE_HOST_NAME = "com.localwebvideodownloader.bridge"
MAX_NATIVE_MESSAGE_BYTES = 64 * 1024
MEDIA_KINDS = frozenset({"dash", "direct", "embed", "hls", "media"})


def _short_text(value: object, *, field_name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"Browser capture field '{field_name}' must be text.")
    text = value.strip()
    if not text or len(text) > limit or any(ord(character) < 32 for character in text):
        raise ValidationError(f"Browser capture field '{field_name}' is invalid.")
    return text


@dataclass(frozen=True, slots=True)
class CapturedSource:
    url: str
    page_url: str
    frame_url: str | None
    kind: str
    content_type: str | None = None

    @property
    def referer_url(self) -> str:
        return self.frame_url or self.page_url

    @classmethod
    def from_mapping(cls, value: object) -> CapturedSource:
        if not isinstance(value, Mapping):
            raise ValidationError("The browser capture payload is malformed.")

        raw_kind = _short_text(value.get("kind"), field_name="kind", limit=24).casefold()
        if raw_kind not in MEDIA_KINDS:
            raise ValidationError("The browser capture media type is unsupported.")

        url = validate_network_url(_short_text(value.get("url"), field_name="url", limit=8192))
        page_url = validate_network_url(
            _short_text(value.get("page_url"), field_name="page_url", limit=8192)
        )

        raw_frame_url = value.get("frame_url")
        frame_url = None
        if raw_frame_url is not None:
            frame_url = validate_network_url(
                _short_text(raw_frame_url, field_name="frame_url", limit=8192)
            )

        raw_content_type = value.get("content_type")
        content_type = None
        if raw_content_type:
            content_type = _short_text(
                raw_content_type, field_name="content_type", limit=160
            ).casefold()

        return cls(
            url=url,
            page_url=page_url,
            frame_url=frame_url,
            kind=raw_kind,
            content_type=content_type,
        )

    @classmethod
    def from_native_message(cls, value: object) -> CapturedSource:
        if not isinstance(value, Mapping):
            raise ValidationError("The native message is malformed.")
        if value.get("version") != SCHEMA_VERSION or value.get("command") != "open_capture":
            raise ValidationError("The native message version or command is unsupported.")
        return cls.from_mapping(value.get("capture"))

    def to_child_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "url": self.url,
            "page_url": self.page_url,
            "frame_url": self.frame_url,
            "kind": self.kind,
            "content_type": self.content_type,
        }

    @classmethod
    def from_child_message(cls, value: object) -> CapturedSource:
        if not isinstance(value, Mapping) or value.get("version") != SCHEMA_VERSION:
            raise ValidationError("The desktop capture message version is unsupported.")
        return cls.from_mapping(value)
