from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from localwebvideodownloader.errors import InvalidUrlError
from localwebvideodownloader.security import (
    redact_sensitive_text,
    referrer_for_target,
    safe_display_url,
    validate_network_url,
    validate_web_url,
)


class ValidateWebUrlTests(unittest.TestCase):
    def test_accepts_http_and_preserves_signed_query(self) -> None:
        url = "HTTPS://media.example/video.m3u8?token=ABC&part=2#fragment"
        self.assertEqual(
            validate_web_url(url),
            "https://media.example/video.m3u8?token=ABC&part=2#fragment",
        )

    def test_rejects_non_web_schemes(self) -> None:
        for value in (
            "file:///C:/private.txt",
            "javascript:alert(1)",
            "data:text/plain,hello",
            r"\\server\share\video.mp4",
        ):
            with self.subTest(value=value), self.assertRaises(InvalidUrlError):
                validate_web_url(value)

    def test_rejects_credentials_missing_host_and_bad_port(self) -> None:
        for value in (
            "https://user:secret@example.test/video",
            "https:///video",
            "https://example.test:99999/video",
            "https://example.test/video\nAuthorization: secret",
        ):
            with self.subTest(value=value), self.assertRaises(InvalidUrlError):
                validate_web_url(value)


class RedactionTests(unittest.TestCase):
    def test_display_url_removes_query_fragment_and_credentials(self) -> None:
        displayed = safe_display_url("https://user:pass@example.test/path/file?token=secret#x")
        self.assertEqual(displayed, "https://example.test/<redacted-path>")

    def test_redacts_urls_and_header_secrets(self) -> None:
        diagnostic = redact_sensitive_text(
            "failed https://cdn.example.test/a.m3u8?token=SECRET "
            "Authorization: Bearer-secret token=ALSOSECRET"
        )
        self.assertNotIn("SECRET", diagnostic)
        self.assertNotIn("Bearer-secret", diagnostic)
        self.assertIn("https://cdn.example.test/<redacted-path>", diagnostic)
        self.assertIn("<redacted>", diagnostic)

    def test_redacts_bearer_values_and_mapping_style_headers(self) -> None:
        diagnostic = redact_sensitive_text(
            "request failed Authorization: Bearer TOPSECRET, "
            "headers={'Cookie': 'session=COOKIESECRET'}"
        )
        self.assertNotIn("TOPSECRET", diagnostic)
        self.assertNotIn("COOKIESECRET", diagnostic)

    def test_redacts_every_cookie_pair_in_raw_header(self) -> None:
        diagnostic = redact_sensitive_text("Cookie: sid=ONE; csrftoken=TWO")
        self.assertNotIn("ONE", diagnostic)
        self.assertNotIn("TWO", diagnostic)

    def test_redacts_cookie_header_on_an_internal_line(self) -> None:
        diagnostic = redact_sensitive_text(
            "request failed\nCookie: sid=ONE; csrftoken=TWO\nstatus 403"
        )
        self.assertNotIn("ONE", diagnostic)
        self.assertNotIn("TWO", diagnostic)
        self.assertIn("status 403", diagnostic)

    def test_redacts_quoted_mapping_secrets_with_spaces(self) -> None:
        diagnostic = redact_sensitive_text(
            "payload {'access_token': 'TOP SECRET', 'signature': 'SIGVALUE'}"
        )
        self.assertNotIn("TOP SECRET", diagnostic)
        self.assertNotIn("SIGVALUE", diagnostic)

    def test_rejects_private_addresses_unless_explicitly_allowed(self) -> None:
        for url in (
            "http://127.0.0.1/video",
            "http://10.1.2.3/video",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/video",
        ):
            with self.subTest(url=url), self.assertRaises(InvalidUrlError):
                validate_network_url(url)
            self.assertEqual(validate_network_url(url, allow_private_network=True), url)

    def test_rejects_hostname_that_resolves_to_private_address(self) -> None:
        private_result = [(2, 1, 6, "", ("192.168.1.10", 443))]
        with (
            patch("localwebvideodownloader.security.socket.getaddrinfo", return_value=private_result),
            self.assertRaises(InvalidUrlError),
        ):
            validate_network_url("https://internal.example.test/video")

    def test_redaction_is_bounded(self) -> None:
        self.assertLessEqual(len(redact_sensitive_text("a" * 5000, limit=80)), 80)


class ReferrerPolicyTests(unittest.TestCase):
    def test_same_origin_still_sends_only_origin(self) -> None:
        self.assertEqual(
            referrer_for_target(
                "https://1.1.1.1/player?id=abc#position",
                "https://1.1.1.1/video.m3u8",
            ),
            "https://1.1.1.1/",
        )

    def test_cross_origin_sends_only_origin(self) -> None:
        self.assertEqual(
            referrer_for_target(
                "https://1.1.1.1/player?id=secret",
                "https://8.8.8.8/video.m3u8",
            ),
            "https://1.1.1.1/",
        )

    def test_https_to_http_downgrade_sends_no_referrer(self) -> None:
        self.assertIsNone(
            referrer_for_target(
                "https://1.1.1.1/player",
                "http://8.8.8.8/video.mp4",
            )
        )


if __name__ == "__main__":
    unittest.main()
