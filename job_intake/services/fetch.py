"""Bounded, defensive URL fetching + main-content extraction (D-004, requirements.md AJ-006).

Extraction library: **readability-lxml** (import name `readability`) -- a small, maintained port
of Mozilla's Readability algorithm, already pulling in `lxml` (used directly here too) and
`cssselect`. Chosen over heavier alternatives (e.g. `trafilatura`, which pulls in its own
crawling/date-parsing dependency chain aimed at bulk corpus scraping) because it is the smallest
maintained option that performs well specifically at "strip chrome, keep the main article/posting
body" for one URL at a time -- exactly this milestone's need, nothing more (D-004 follow-up,
recorded in docs/DECISIONS.md).

This module never sends application secrets or provider credentials in a fetch request, never lets
a raw fetched body reach an exception message/log line, and is fully mockable in tests (every
automated test replaces `requests.get` -- zero real network access).
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from lxml import html as lxml_html
from readability import Document

ALLOWED_SCHEMES = frozenset({"http", "https"})
MAX_REDIRECTS = 5
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 2_000_000  # 2 MB
ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
USER_AGENT = "cvbuilder-job-intake/1.0 (local single-operator tool; +https://example.invalid)"
MIN_USABLE_TEXT_CHARS = 200
_CHALLENGE_MARKERS = (
    "access denied",
    "are you a human",
    "are you a robot",
    "enable javascript",
    "just a moment",
    "verify you are human",
    "checking your browser",
    "captcha",
    "403 forbidden",
    "attention required",
)


class FetchError(Exception):
    """Raised for every fetch/extraction failure. `safe_message` is the only text ever shown to
    the operator or included in a UI-facing string -- the exception's own `str()` may reference
    internal detail (status codes, exception types) but never a raw fetched response body."""

    def __init__(self, message: str, *, safe_message: str):
        super().__init__(message)
        self.safe_message = safe_message


@dataclass(frozen=True)
class FetchResult:
    text: str
    title: str
    final_url: str


def _reject_unsafe_host(hostname: str) -> None:
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        raise FetchError(
            f"DNS resolution failed for {hostname!r}: {exc}",
            safe_message="Could not resolve this URL's host.",
        ) from exc
    for info in addr_infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise FetchError(
                f"{hostname!r} resolves to a disallowed address ({ip}).",
                safe_message="This URL points to a private or internal network address, which is "
                "not allowed.",
            )


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise FetchError(
            f"Disallowed scheme {parsed.scheme!r}",
            safe_message="Only http:// and https:// URLs are supported.",
        )
    if parsed.username or parsed.password:
        raise FetchError(
            "URL contains embedded credentials",
            safe_message="URLs with embedded credentials are not allowed.",
        )
    if not parsed.hostname:
        raise FetchError("URL has no hostname", safe_message="This does not look like a valid URL.")
    _reject_unsafe_host(parsed.hostname)


def _is_usable_text(text: str) -> bool:
    if len(text) < MIN_USABLE_TEXT_CHARS:
        return False
    lowered = text.lower()
    return not any(marker in lowered for marker in _CHALLENGE_MARKERS)


def _extract_main_content(html_text: str) -> tuple[str, str]:
    try:
        doc = Document(html_text)
        summary_html = doc.summary()
        title = doc.short_title() or ""
        content_tree = lxml_html.fromstring(summary_html)
        raw_text = content_tree.text_content()
    except Exception as exc:  # readability/lxml can raise a variety of parse errors
        raise FetchError(
            f"Main-content extraction failed: {type(exc).__name__}",
            safe_message="Could not extract readable content from this page.",
        ) from exc
    text = "\n".join(line.strip() for line in raw_text.splitlines() if line.strip())
    return text, title


def fetch_job_posting(url: str) -> FetchResult:
    """HTTP fetch -> main-content extraction -> usability check (D-004). Raises `FetchError` with
    a safe, operator-facing message on any failure; never returns a low-quality/unusable result
    silently. Every redirect hop is independently validated against the same SSRF protections as
    the original URL."""
    _validate_url(url)
    current_url = url

    for _ in range(MAX_REDIRECTS + 1):
        try:
            response = requests.get(
                current_url,
                headers={"User-Agent": USER_AGENT},
                timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
                allow_redirects=False,
                stream=True,
            )
        except requests.Timeout as exc:
            raise FetchError(str(exc), safe_message="The request timed out.") from exc
        except requests.RequestException as exc:
            raise FetchError(str(exc), safe_message="Could not connect to this URL.") from exc

        with response:
            if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise FetchError(
                        "Redirect with no Location header",
                        safe_message="This URL redirected without a destination.",
                    )
                current_url = urljoin(current_url, location)
                _validate_url(current_url)
                continue

            if response.status_code != 200:
                raise FetchError(
                    f"status={response.status_code}",
                    safe_message=f"The server returned an error (status {response.status_code}).",
                )

            content_type = response.headers.get("Content-Type", "")
            if not any(allowed in content_type for allowed in ALLOWED_CONTENT_TYPES):
                raise FetchError(
                    f"content-type={content_type!r}",
                    safe_message="This URL did not return a web page (unsupported content type).",
                )

            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    if int(content_length) > MAX_RESPONSE_BYTES:
                        raise FetchError(
                            "Content-Length exceeds limit", safe_message="This page is too large to fetch."
                        )
                except ValueError:
                    pass  # malformed header -- fall through to the streamed size check below

            body = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise FetchError(
                        "Response body exceeds limit (streamed)",
                        safe_message="This page is too large to fetch.",
                    )
            html_text = body.decode(response.encoding or "utf-8", errors="replace")

        text, title = _extract_main_content(html_text)
        if not _is_usable_text(text):
            raise FetchError(
                "Extracted text too short or matched a challenge/access-denied marker",
                safe_message="This page did not contain a usable job posting -- it may require "
                "JavaScript, block automated access, or show a verification page. Paste the "
                "posting text instead.",
            )
        return FetchResult(text=text, title=title, final_url=current_url)

    raise FetchError("Too many redirects", safe_message="This URL redirected too many times.")
