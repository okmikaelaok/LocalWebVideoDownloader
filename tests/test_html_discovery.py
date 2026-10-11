from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from localwebvideodownloader.html_discovery import discover_embedded_urls, parse_page_links


class HtmlParsingTests(unittest.TestCase):
    def test_finds_and_resolves_iframe_video_source_and_metadata(self) -> None:
        links = parse_page_links(
            """
            <iframe src="/player/index.html"></iframe>
            <video src="clips/main.mp4"><source src="/fallback.webm"></video>
            <meta property="og:video" content="https://cdn.example.test/master.m3u8">
            <iframe src="javascript:alert(1)"></iframe>
            """,
            "https://www.example.test/watch/page",
        )
        self.assertEqual(links.iframe_urls, ("https://www.example.test/player/index.html",))
        self.assertEqual(
            links.media_urls,
            (
                "https://www.example.test/watch/clips/main.mp4",
                "https://www.example.test/fallback.webm",
                "https://cdn.example.test/master.m3u8",
            ),
        )

    def test_bounded_recursive_discovery_deduplicates_urls(self) -> None:
        pages = {
            "https://example.test/root": (
                "https://example.test/root",
                '<iframe src="/frame1"></iframe><video src="/shared.mp4"></video>',
            ),
            "https://example.test/frame1": (
                "https://example.test/frame1",
                '<iframe src="/frame2"></iframe><source src="/shared.mp4">',
            ),
            "https://example.test/frame2": (
                "https://example.test/frame2",
                '<video src="/deep.mp4"></video>',
            ),
        }
        fetched: list[str] = []

        def fetcher(url: str) -> tuple[str, str] | None:
            fetched.append(url)
            return pages.get(url)

        found = discover_embedded_urls(
            "https://example.test/root",
            max_depth=1,
            max_pages=8,
            allow_private_network=True,
            fetcher=fetcher,
        )
        self.assertEqual(
            found,
            (
                "https://example.test/shared.mp4",
                "https://example.test/frame1",
                "https://example.test/frame2",
            ),
        )
        self.assertEqual(fetched, ["https://example.test/root", "https://example.test/frame1"])

    def test_max_pages_stops_recursive_frame_tree(self) -> None:
        def fetcher(url: str) -> tuple[str, str]:
            number = int(url.rsplit("/", 1)[-1])
            return url, f'<iframe src="/{number + 1}"></iframe>'

        found = discover_embedded_urls(
            "https://example.test/0",
            max_depth=20,
            max_pages=3,
            allow_private_network=True,
            fetcher=fetcher,
        )
        self.assertEqual(
            found,
            (
                "https://example.test/1",
                "https://example.test/2",
                "https://example.test/3",
            ),
        )

    def test_max_candidates_caps_hostile_link_fanout(self) -> None:
        many_sources = "".join(f'<source src="/{number}.mp4">' for number in range(1000))

        def fetcher(url: str) -> tuple[str, str]:
            return url, many_sources

        found = discover_embedded_urls(
            "https://example.test/root",
            max_candidates=7,
            allow_private_network=True,
            fetcher=fetcher,
        )
        self.assertEqual(len(found), 7)


if __name__ == "__main__":
    unittest.main()
