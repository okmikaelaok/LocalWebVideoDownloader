"""Input validation and diagnostic redaction helpers."""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit

from localwebvideodownloader.errors import InvalidUrlError

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(['\"]?\b(?:token|access_token|auth|authorization|signature|sig|key|cookie|set-cookie)"
    r"\b['\"]?\s*[:=]\s*)(?:['\"][^'\"\r\n]*['\"]|[^\s,;}]+)"
)
_SENSITIVE_HEADER_VALUE = re.compile(
    r"(?i)(?:['\"]?\b(authorization|cookie|set-cookie|proxy-authorization)\b['\"]?"
    r"\s*[:=]\s*)(?:['\"][^'\"\r\n]*['\"]|[^\r\n,;}]+)"
)
_COOKIE_TO_END = re.compile(r"(?i)(?:['\"]?\b(cookie|set-cookie)\b['\"]?\s*[:=]\s*).*$")
_COOKIE_HEADER_LINE = re.compile(r"(?im)^(\s*(?:cookie|set-cookie)\s*:)[^\r\n]*")
_AUTHORIZATION_TOKEN = re.compile(
    r"(?i)(\b(?:authorization|proxy-authorization)\b\s*[:=]\s*)"
    r"(?:bearer|basic)\s+[^\s,;}\]]+"
)


def validate_web_url(value: str) -> str:
    """Return a normalized HTTP(S) URL or raise a safe validation error."""

    url = value.strip()
    if not url:
        raise InvalidUrlError("Enter a page or media URL.")
    if len(url) > 8192:
        raise InvalidUrlError("The URL is too long.")
    if _CONTROL_CHARACTERS.search(url):
        raise InvalidUrlError("The URL contains control characters.")

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise InvalidUrlError("The URL is malformed.") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise InvalidUrlError("Only http:// and https:// URLs are supported.")
    if not parsed.hostname:
        raise InvalidUrlError("The URL must include a host name.")
    if parsed.username is not None or parsed.password is not None:
        raise InvalidUrlError("URLs containing a username or password are not supported.")
    if port is not None and not 1 <= port <= 65535:
        raise InvalidUrlError("The URL contains an invalid port.")

    # Normalize only the scheme. Preserve path, query, and fragment exactly because
    # signed media links can be case-sensitive.
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, parsed.fragment)
    )


def safe_display_url(value: str) -> str:
    """Show only a URL's public origin, never a possibly signed path or query."""

    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<redacted-url>"
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return "<redacted-url>"

    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        return "<redacted-url>"
    if port:
        host = f"{host}:{port}"
    path_marker = "/<redacted-path>" if parsed.path and parsed.path != "/" else parsed.path
    return urlunsplit((parsed.scheme.lower(), host, path_marker, "", ""))


def validate_network_url(value: str, *, allow_private_network: bool = False) -> str:
    """Validate a web URL and reject local/private destinations by default.

    Hostnames must resolve successfully, and every returned address must be
    globally routable.
    """

    url = validate_web_url(value)
    if allow_private_network:
        return url
    parsed = urlsplit(url)
    assert parsed.hostname is not None  # guaranteed by validate_web_url
    addresses: set[str] = set()
    try:
        addresses.add(str(ipaddress.ip_address(parsed.hostname.split("%", 1)[0])))
    except ValueError:
        try:
            resolved = socket.getaddrinfo(
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except (OSError, UnicodeError) as exc:
            raise InvalidUrlError(
                "The URL host could not be resolved safely. Check the address and try again."
            ) from exc
        addresses.update(result[4][0].split("%", 1)[0] for result in resolved)

    try:
        blocked = any(not ipaddress.ip_address(address).is_global for address in addresses)
    except ValueError as exc:
        raise InvalidUrlError("The URL host could not be validated safely.") from exc
    if blocked:
        raise InvalidUrlError(
            "Local, private, link-local, and reserved network URLs are blocked by default. "
            "Use the explicit private-network option only for a trusted local test page."
        )
    return url


def referrer_for_target(
    referrer: str,
    target: str,
    *,
    allow_private_network: bool = False,
) -> str | None:
    """Build an origin-only Referer value for one validated target.

    Browser captures can contain signed page paths and queries. Sending only
    the initiating origin prevents those values from leaking if a downloader
    request later redirects across origins. HTTPS-to-HTTP downgrades do not
    receive a Referer at all.
    """

    safe_referrer = validate_network_url(referrer, allow_private_network=allow_private_network)
    safe_target = validate_network_url(target, allow_private_network=allow_private_network)
    referrer_parts = urlsplit(safe_referrer)
    target_parts = urlsplit(safe_target)

    if referrer_parts.scheme == "https" and target_parts.scheme == "http":
        return None

    return urlunsplit((referrer_parts.scheme, referrer_parts.netloc, "/", "", ""))


def redact_sensitive_text(value: object, *, limit: int = 1200) -> str:
    """Return a bounded diagnostic with likely credentials removed."""

    text = str(value)
    text = _COOKIE_HEADER_LINE.sub(lambda match: f"{match.group(1)} <redacted>", text)
    text = _COOKIE_TO_END.sub(lambda match: f"{match.group(1)}: <redacted>", text)
    text = _AUTHORIZATION_TOKEN.sub(lambda match: f"{match.group(1)}<redacted>", text)
    text = _SENSITIVE_HEADER_VALUE.sub(lambda match: f"{match.group(1)}: <redacted>", text)
    text = _URL_IN_TEXT.sub(lambda match: safe_display_url(match.group(0)), text)
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}<redacted>", text)
    text = _CONTROL_CHARACTERS.sub(" ", text)
    text = " ".join(text.split())
    if len(text) > limit:
        return f"{text[: limit - 3]}..."
    return text
