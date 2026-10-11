"""Bounded static discovery for generic HTML pages and iframe chains.

This is deliberately a parser, not a browser. It never evaluates scripts and it
does not import browser cookies. Discovered URLs are handed back to yt-dlp.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener

from localwebvideodownloader.errors import InvalidUrlError
from localwebvideodownloader.security import validate_network_url, validate_web_url

MAX_HTML_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PageLinks:
    iframe_urls: tuple[str, ...]
    media_urls: tuple[str, ...]


class _MediaHTMLParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.iframes: list[str] = []
        self.media: list[str] = []
        self._iframe_seen: set[str] = set()
        self._media_seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.casefold(): value for name, value in attrs if value is not None}
        tag = tag.casefold()
        if tag in {"iframe", "embed"}:
            self._add(self.iframes, self._iframe_seen, attributes.get("src"))
            return
        if tag in {"video", "source"}:
            self._add(self.media, self._media_seen, attributes.get("src"))
            return
        if tag == "meta":
            name = (attributes.get("property") or attributes.get("name") or "").casefold()
            if name in {
                "og:video",
                "og:video:url",
                "og:video:secure_url",
                "twitter:player:stream",
            }:
                self._add(self.media, self._media_seen, attributes.get("content"))
            elif name == "twitter:player":
                self._add(self.iframes, self._iframe_seen, attributes.get("content"))

    def _add(self, destination: list[str], seen: set[str], value: str | None) -> None:
        if not value:
            return
        absolute, _fragment = urldefrag(urljoin(self.page_url, value.strip()))
        try:
            normalized = validate_web_url(absolute)
        except InvalidUrlError:
            return
        if normalized not in seen:
            seen.add(normalized)
            destination.append(normalized)


def parse_page_links(html: str, page_url: str) -> PageLinks:
    """Parse supported iframe and media references from an HTML string."""

    parser = _MediaHTMLParser(page_url)
    parser.feed(html)
    parser.close()
    return PageLinks(tuple(parser.iframes), tuple(parser.media))


class _ValidatedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, *, allow_private_network: bool) -> None:
        super().__init__()
        self.allow_private_network = allow_private_network

    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> Request | None:
        validate_network_url(newurl, allow_private_network=self.allow_private_network)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch_html(
    url: str, *, timeout: float = 8.0, allow_private_network: bool = False
) -> tuple[str, str] | None:
    url = validate_network_url(url, allow_private_network=allow_private_network)
    request = Request(
        url,
        headers={
            "User-Agent": "LocalWebVideoDownloader/0.1 (+local desktop application)",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        },
    )
    try:
        opener = build_opener(
            _ValidatedRedirectHandler(allow_private_network=allow_private_network)
        )
        with opener.open(request, timeout=timeout) as response:
            final_url = validate_network_url(
                response.geturl(), allow_private_network=allow_private_network
            )
            content_type = response.headers.get_content_type().casefold()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                return None
            declared_length = response.headers.get("Content-Length")
            if declared_length:
                try:
                    if int(declared_length) > MAX_HTML_BYTES:
                        return None
                except ValueError:
                    pass
            payload = response.read(MAX_HTML_BYTES + 1)
            if len(payload) > MAX_HTML_BYTES:
                return None
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                html = payload.decode(charset, errors="replace")
            except LookupError:
                html = payload.decode("utf-8", errors="replace")
            return final_url, html
    except (
        HTTPError,
        URLError,
        HTTPException,
        OSError,
        TimeoutError,
        ValueError,
        InvalidUrlError,
    ):
        return None


def discover_embedded_urls(
    root_url: str,
    *,
    max_depth: int = 2,
    max_pages: int = 8,
    max_candidates: int = 32,
    allow_private_network: bool = False,
    fetcher: Callable[[str], tuple[str, str] | None] | None = None,
) -> tuple[str, ...]:
    """Find media and iframe URLs without evaluating page scripts.

    At most ``max_pages`` HTML documents are fetched. Iframes are followed only
    through ``max_depth`` levels, preventing recursive or hostile frame trees from
    causing unbounded work.
    """

    root = validate_network_url(root_url, allow_private_network=allow_private_network)
    if max_depth < 0 or max_pages < 1 or max_candidates < 1:
        return ()

    queue: deque[tuple[str, int]] = deque([(root, 0)])
    visited_pages: set[str] = set()
    candidates: list[str] = []
    seen_candidates: set[str] = {root}

    while queue and len(visited_pages) < max_pages and len(candidates) < max_candidates:
        page_url, depth = queue.popleft()
        if page_url in visited_pages:
            continue
        visited_pages.add(page_url)
        if fetcher is None:
            fetched = _fetch_html(page_url, allow_private_network=allow_private_network)
        else:
            fetched = fetcher(page_url)
        if fetched is None:
            continue
        final_url, html = fetched
        links = parse_page_links(html, final_url)
        for media_url in links.media_urls:
            if len(candidates) >= max_candidates:
                break
            try:
                media_url = validate_network_url(
                    media_url, allow_private_network=allow_private_network
                )
            except InvalidUrlError:
                continue
            if media_url not in seen_candidates:
                seen_candidates.add(media_url)
                candidates.append(media_url)
        for iframe_url in links.iframe_urls:
            if len(candidates) >= max_candidates:
                break
            try:
                iframe_url = validate_network_url(
                    iframe_url, allow_private_network=allow_private_network
                )
            except InvalidUrlError:
                continue
            if iframe_url not in seen_candidates:
                seen_candidates.add(iframe_url)
                candidates.append(iframe_url)
            if depth < max_depth and iframe_url not in visited_pages:
                queue.append((iframe_url, depth + 1))
    return tuple(candidates)
