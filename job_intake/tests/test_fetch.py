"""URL fetch safety and main-content extraction -- every test mocks `requests.get`/DNS resolution;
zero real network access."""

from __future__ import annotations

import socket
from unittest import mock

import requests
from django.test import SimpleTestCase

from ..services.fetch import FetchError, fetch_job_posting

_SAMPLE_HTML = """
<html><head><title>Senior Backend Engineer</title></head>
<body>
<nav>Home | About | Careers</nav>
<article>
<h1>Senior Backend Engineer</h1>
<p>We are looking for a Senior Backend Engineer with 5+ years of Python experience to join our
payments team. You will own the payments service end to end and collaborate closely with product
and design. Experience with Django and PostgreSQL is a strong plus. This is a remote-friendly
role based out of our Springfield office.</p>
</article>
<footer>Copyright 2026</footer>
</body></html>
"""


def _public_addr_info(*args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def _fake_response(status_code=200, headers=None, body=b"", location=None):
    response = mock.MagicMock()
    response.status_code = status_code
    headers = dict(headers or {})
    if location:
        headers["Location"] = location
    response.headers = headers
    response.is_redirect = status_code in (301, 302, 303, 307, 308)
    response.encoding = "utf-8"
    response.iter_content.side_effect = lambda chunk_size=8192: iter([body] if body else [])
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class UrlSafetyValidationTests(SimpleTestCase):
    def test_rejects_non_http_scheme(self):
        with self.assertRaises(FetchError):
            fetch_job_posting("file:///etc/passwd")

    def test_rejects_embedded_credentials(self):
        with self.assertRaises(FetchError):
            fetch_job_posting("https://user:pass@example.com/job")

    @mock.patch("socket.getaddrinfo")
    def test_rejects_loopback_address(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
        with self.assertRaises(FetchError):
            fetch_job_posting("http://localhost/job")

    @mock.patch("socket.getaddrinfo")
    def test_rejects_private_network_address(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]
        with self.assertRaises(FetchError):
            fetch_job_posting("http://internal.example/job")

    @mock.patch("socket.getaddrinfo")
    def test_rejects_link_local_address(self, mock_getaddrinfo):
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]
        with self.assertRaises(FetchError):
            fetch_job_posting("http://169.254.169.254/latest/meta-data/")

    @mock.patch("socket.getaddrinfo")
    def test_dns_resolution_failure_raises(self, mock_getaddrinfo):
        mock_getaddrinfo.side_effect = OSError("name resolution failed")
        with self.assertRaises(FetchError):
            fetch_job_posting("http://nonexistent.invalid/job")


@mock.patch("socket.getaddrinfo", side_effect=_public_addr_info)
class NetworkBehaviorTests(SimpleTestCase):
    @mock.patch("requests.get")
    def test_valid_html_extracts_usable_main_content(self, mock_get, _mock_dns):
        mock_get.return_value = _fake_response(
            200, headers={"Content-Type": "text/html; charset=utf-8"}, body=_SAMPLE_HTML.encode()
        )
        result = fetch_job_posting("https://example.com/job/123")
        self.assertIn("Senior Backend Engineer", result.text)
        self.assertIn("Python experience", result.text)
        self.assertNotIn("Home | About | Careers", result.text)

    @mock.patch("requests.get")
    def test_timeout_raises_fetch_error(self, mock_get, _mock_dns):
        mock_get.side_effect = requests.Timeout("timed out")
        with self.assertRaises(FetchError):
            fetch_job_posting("https://example.com/job")

    @mock.patch("requests.get")
    def test_connection_error_raises_fetch_error(self, mock_get, _mock_dns):
        mock_get.side_effect = requests.ConnectionError("connection refused")
        with self.assertRaises(FetchError):
            fetch_job_posting("https://example.com/job")

    @mock.patch("requests.get")
    def test_bad_status_raises_fetch_error(self, mock_get, _mock_dns):
        mock_get.return_value = _fake_response(500, headers={"Content-Type": "text/html"})
        with self.assertRaises(FetchError):
            fetch_job_posting("https://example.com/job")

    @mock.patch("requests.get")
    def test_excessive_redirects_raise_fetch_error(self, mock_get, _mock_dns):
        mock_get.return_value = _fake_response(
            302, headers={"Location": "https://example.com/next"}
        )
        with self.assertRaises(FetchError):
            fetch_job_posting("https://example.com/job")

    @mock.patch("requests.get")
    def test_redirect_to_unsafe_address_rejected(self, mock_get, mock_dns):
        first = _fake_response(302, headers={"Location": "http://169.254.169.254/secret"})
        mock_get.return_value = first

        def dns_side_effect(host, *args, **kwargs):
            if host == "169.254.169.254":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]
            return _public_addr_info()

        mock_dns.side_effect = dns_side_effect
        with self.assertRaises(FetchError):
            fetch_job_posting("https://example.com/job")

    @mock.patch("requests.get")
    def test_unsupported_content_type_rejected(self, mock_get, _mock_dns):
        mock_get.return_value = _fake_response(
            200, headers={"Content-Type": "application/pdf"}, body=b"%PDF-1.4"
        )
        with self.assertRaises(FetchError):
            fetch_job_posting("https://example.com/job.pdf")

    @mock.patch("requests.get")
    def test_oversized_content_length_rejected(self, mock_get, _mock_dns):
        mock_get.return_value = _fake_response(
            200, headers={"Content-Type": "text/html", "Content-Length": str(10_000_000)}
        )
        with self.assertRaises(FetchError):
            fetch_job_posting("https://example.com/job")

    @mock.patch("requests.get")
    def test_oversized_streamed_body_rejected_even_without_content_length_header(self, mock_get, _mock_dns):
        big_body = b"x" * (2_100_000)
        mock_get.return_value = _fake_response(
            200, headers={"Content-Type": "text/html"}, body=big_body
        )
        with self.assertRaises(FetchError):
            fetch_job_posting("https://example.com/job")

    @mock.patch("requests.get")
    def test_empty_extraction_rejected(self, mock_get, _mock_dns):
        mock_get.return_value = _fake_response(
            200, headers={"Content-Type": "text/html"}, body=b"<html><body></body></html>"
        )
        with self.assertRaises(FetchError):
            fetch_job_posting("https://example.com/job")

    @mock.patch("requests.get")
    def test_too_short_extraction_rejected(self, mock_get, _mock_dns):
        mock_get.return_value = _fake_response(
            200, headers={"Content-Type": "text/html"},
            body=b"<html><body><p>Hi.</p></body></html>",
        )
        with self.assertRaises(FetchError):
            fetch_job_posting("https://example.com/job")

    @mock.patch("requests.get")
    def test_challenge_page_rejected(self, mock_get, _mock_dns):
        challenge_html = (
            "<html><body><h1>Just a moment...</h1><p>"
            + ("Please wait while we verify you are human. " * 10)
            + "</p></body></html>"
        )
        mock_get.return_value = _fake_response(
            200, headers={"Content-Type": "text/html"}, body=challenge_html.encode()
        )
        with self.assertRaises(FetchError):
            fetch_job_posting("https://example.com/job")

    @mock.patch("job_intake.services.fetch.Document")
    @mock.patch("requests.get")
    def test_extraction_library_failure_raises_fetch_error(self, mock_get, mock_document, _mock_dns):
        mock_get.return_value = _fake_response(
            200, headers={"Content-Type": "text/html"}, body=_SAMPLE_HTML.encode()
        )
        mock_document.side_effect = ValueError("boom")
        with self.assertRaises(FetchError):
            fetch_job_posting("https://example.com/job")
