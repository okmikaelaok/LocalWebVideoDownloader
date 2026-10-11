"""Portable output basenames, never filesystem paths or yt-dlp templates."""

from __future__ import annotations

import re
import unicodedata

from localwebvideodownloader.errors import DownloadError

# Leave room for language/format IDs, automatic extensions and resumable suffixes.
MAX_OUTPUT_NAME_BYTES = 160
_FORBIDDEN = frozenset('<>:"/\\|?*')
_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])$", re.I)


def _is_control(character: str) -> bool:
    return unicodedata.category(character) in {"Cc", "Cf", "Cs"}


def _is_reserved(value: str) -> bool:
    # Windows device names remain reserved even when followed by an extension.
    return _RESERVED.fullmatch(value.split(".", 1)[0].rstrip(" ")) is not None


def validate_output_name(value: str | None) -> str | None:
    """Validate a literal filename stem consistently on Windows and other hosts.

    An empty value uses the backend's existing title-and-ID naming scheme. The
    downloader adds the container extension and caption language automatically.
    Percent signs are legal here; the template builder must escape them.
    """

    if value is None:
        return None
    if not isinstance(value, str):
        raise DownloadError("The file name must be text.")
    if any(_is_control(character) for character in value):
        raise DownloadError(
            "The file name cannot contain control or invisible formatting characters."
        )
    if not value.strip():
        return None
    if any(character in _FORBIDDEN for character in value):
        raise DownloadError(
            'Enter a file name, not a path. These characters are not allowed: <>:"/\\|?*'
        )
    if value != value.strip() or value.endswith("."):
        raise DownloadError("The file name cannot start or end with spaces, or end with a dot.")
    if _is_reserved(value):
        raise DownloadError("That file name is reserved by Windows. Choose a different name.")
    if len(value.encode("utf-8")) > MAX_OUTPUT_NAME_BYTES:
        raise DownloadError(
            f"The file name is too long. Use at most {MAX_OUTPUT_NAME_BYTES} UTF-8 bytes "
            "(fewer characters for some languages)."
        )
    return value


def suggest_output_name(title: str) -> str:
    """Return an editable, valid filename stem based on an inspected title."""

    candidate = (
        "".join(
            "_" if character in _FORBIDDEN or _is_control(character) else character
            for character in title
        )
        .strip()
        .rstrip(". ")
    )
    if not candidate:
        candidate = "video"
    if _is_reserved(candidate):
        candidate = "_" + candidate
    # Decode only complete Unicode characters when applying the byte budget.
    candidate = candidate.encode("utf-8")[:MAX_OUTPUT_NAME_BYTES].decode("utf-8", "ignore")
    candidate = candidate.rstrip().rstrip(". ") or "video"
    return candidate


def output_templates(value: str | None) -> dict[str, str]:
    """Produce safe media/caption templates without interpreting user input."""

    name = validate_output_name(value)
    if name is None:
        return {
            "default": "%(title).140B [%(id).64B].%(ext)s",
            "subtitle": "%(title).120B [%(id).64B].%(language).32B.%(ext)s",
        }
    literal = name.replace("%", "%%")
    return {
        "default": f"{literal}.%(ext)s",
        # yt-dlp appends the actual caption language before its extension.
        "subtitle": f"{literal}.%(ext)s",
    }
