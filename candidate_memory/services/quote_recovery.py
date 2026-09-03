"""Deterministic, whitespace-normalized quote-location recovery for provenance verification
(D-017 fix, 2026-09-02).

`services/storage.py::_verify_quote_at_lines` requires an exact substring match between a model-
supplied `support.quote` and the source document's real content at the model-supplied line range.
This is correct and exact, but a real gap was found live: when a supporting sentence word-wraps
across two physical source lines, a model may reconstruct its quote by joining the wrapped halves
with a single space where the source has an actual newline -- and may also mis-report a line range
that doesn't actually span both physical lines. Both cases make the exact check fail even though
the model's semantic extraction was otherwise correct.

This module is a fallback, tried only after the exact check has already failed -- it never
weakens that invariant. It locates the quote in a *whitespace-normalized* view of the source
(any run of whitespace, including newlines, collapsed to a single space), but the match itself is
still exact under that normalization, never fuzzy or semantic, and only a *unique* normalized
match is ever accepted (multiple or zero matches fail closed -- never guessed between). Once
located, the ORIGINAL source slice at that position (real newlines/spacing intact) is recovered
and returned; the caller then re-runs the existing exact-match validator against that recovered
slice, so the actual trust boundary stays "this is byte-for-byte, exactly, a substring of the
immutable source" -- recovery only computes *which* substring that is. The normalized text itself
is never stored anywhere.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class RecoveredQuote:
    text: str  # the exact original source slice -- real newlines/spacing preserved, never normalized
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive


def _normalize_with_offset_map(text: str) -> tuple[str, list[int]]:
    """Collapses any run of whitespace (including newlines/tabs) to a single space and strips
    leading/trailing whitespace. Returns `(normalized_text, offset_map)` where `offset_map[i]` is
    the index into the original `text` that `normalized_text[i]` came from -- so any position
    found in the normalized text can always be mapped back to an exact original offset."""
    normalized_chars: list[str] = []
    offset_map: list[int] = []
    prev_was_space = True  # starts True so leading whitespace is skipped, never emitted
    for i, ch in enumerate(text):
        if ch.isspace():
            if not prev_was_space:
                normalized_chars.append(" ")
                offset_map.append(i)
                prev_was_space = True
        else:
            normalized_chars.append(ch)
            offset_map.append(i)
            prev_was_space = False
    while normalized_chars and normalized_chars[-1] == " ":
        normalized_chars.pop()
        offset_map.pop()
    return "".join(normalized_chars), offset_map


def _find_all(haystack: str, needle: str) -> list[int]:
    if not needle:
        return []
    positions = []
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + 1  # overlapping matches still count -- any second match means ambiguous
    return positions


def recover_quote(raw_content: str, quote: str) -> RecoveredQuote | None:
    """Attempts to locate `quote` in `raw_content` under whitespace normalization.

    Returns the recovered original slice and its real 1-based line numbers only when the
    normalized quote matches *exactly once* in the normalized source. Returns `None` (fail closed)
    for a blank quote, zero matches, or more than one match -- the caller must never guess between
    ambiguous candidates or accept a normalized string as provenance; only the recovered original
    slice, re-verified by the exact-match validator, is ever trusted.
    """
    normalized_quote, _ = _normalize_with_offset_map(quote)
    if not normalized_quote:
        return None

    normalized_source, offset_map = _normalize_with_offset_map(raw_content)
    matches = _find_all(normalized_source, normalized_quote)
    if len(matches) != 1:
        return None

    norm_start = matches[0]
    norm_end = norm_start + len(normalized_quote) - 1
    orig_start = offset_map[norm_start]
    orig_end = offset_map[norm_end]

    exact_slice = raw_content[orig_start : orig_end + 1]
    start_line = raw_content.count("\n", 0, orig_start) + 1
    end_line = raw_content.count("\n", 0, orig_end + 1) + 1
    return RecoveredQuote(text=exact_slice, start_line=start_line, end_line=end_line)
