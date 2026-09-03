"""Bounded, line-number-preserving chunking of source documents (requirements.md Sec 16).

Never send a complete source document to an LLM call in one request -- split into bounded
sections first, each one small enough to keep prompts and outputs manageable, while preserving
the *original* document's line numbers so provenance stays exact.

Audit repair: bounded by *character* count (a deterministic, provider-independent proxy for
prompt/token size -- not exact provider billing, see `services/dry_run.py`'s estimate) in addition
to the original line-count bound, so a chunk cannot grow unboundedly large just because its lines
happen to be long. A single line that alone exceeds the character bound is safely split into
several fragments, each still tagged with that same original line number -- provenance validation
(`services/storage.py::_verify_quote_at_lines`) always re-derives the excerpt from the source
document's own immutable `raw_content` at that line number, never from a chunk's rendered text, so
splitting a long line for prompting purposes never affects provenance correctness.
"""

from __future__ import annotations

import dataclasses
import re

DEFAULT_CHUNK_LINES = 80
DEFAULT_MAX_CHUNK_CHARS = 8000

# Candidate Memory recovery (2026-09-03): the smallest a truncation-triggered split is ever
# allowed to shrink a chunk to. Below this, splitting further stops being a meaningful recovery
# strategy (a handful of lines truncating at 4096 output tokens signals a genuine
# per-item/model-behavior problem, not a chunk-size problem) -- the caller must fail closed
# rather than split forever.
MIN_SPLIT_CHUNK_LINES = 5


@dataclasses.dataclass(frozen=True)
class SourceChunk:
    start_line: int  # 1-based, inclusive, relative to the original document
    end_line: int  # 1-based, inclusive
    lines: tuple[str, ...]  # normally one entry per line start_line..end_line; see module docstring
    # for the single-fragment-of-one-long-line case (start_line == end_line, len(lines) == 1).
    # Candidate Memory recovery (2026-09-03): start_char/end_char are set only for a sentence-level
    # sub-line fragment produced by `split_line_into_sentences` -- a 0-based, end-exclusive Python
    # slice `line[start_char:end_char]` into the single line at start_line==end_line. None (the
    # default, and every other chunk in the codebase) means "whole line(s)"; this is what lets
    # multiple fragments of the *same* physical line be distinguished from one another downstream
    # (`ChunkExtractionAttempt.start_char/end_char`) when start_line/end_line alone cannot.
    start_char: int | None = None
    end_char: int | None = None


def chunk_source(
    raw_content: str,
    chunk_lines: int = DEFAULT_CHUNK_LINES,
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
) -> list[SourceChunk]:
    lines = raw_content.splitlines()
    if not lines:
        return []

    chunks: list[SourceChunk] = []
    current_lines: list[str] = []
    current_start: int | None = None
    current_chars = 0

    def flush() -> None:
        nonlocal current_lines, current_start, current_chars
        if current_lines:
            chunks.append(
                SourceChunk(
                    start_line=current_start,
                    end_line=current_start + len(current_lines) - 1,
                    lines=tuple(current_lines),
                )
            )
        current_lines = []
        current_start = None
        current_chars = 0

    for line_number, line in enumerate(lines, start=1):
        line_len = len(line)

        if line_len > max_chunk_chars:
            # A single unavoidable edge case: one line alone exceeds the bound. Flush whatever was
            # accumulated so far, then safely split just this line into bounded fragments, each
            # tagged with this same original line number -- never silently included whole.
            flush()
            for frag_start in range(0, line_len, max_chunk_chars):
                fragment = line[frag_start : frag_start + max_chunk_chars]
                chunks.append(SourceChunk(start_line=line_number, end_line=line_number, lines=(fragment,)))
            continue

        would_overflow_lines = len(current_lines) >= chunk_lines
        would_overflow_chars = current_lines and current_chars + line_len + 1 > max_chunk_chars
        if current_lines and (would_overflow_lines or would_overflow_chars):
            flush()

        if current_start is None:
            current_start = line_number
        current_lines.append(line)
        current_chars += line_len + 1  # +1 for the joining newline in the rendered prompt

    flush()
    return chunks


def split_chunk_in_half(chunk: SourceChunk) -> tuple[SourceChunk, SourceChunk] | None:
    """Splits `chunk` into two smaller `SourceChunk`s by line count, each preserving its own
    original-document line numbers exactly (provenance is never affected by how a chunk was
    split -- `storage.py::_verify_quote_at_lines` always re-derives from the source document's
    own immutable content at the stated lines, never from a chunk's rendered text).

    Returns `None` when `chunk` is already at or below `MIN_SPLIT_CHUNK_LINES` and cannot be
    safely split further -- the caller must then fail closed rather than split forever.
    """
    if len(chunk.lines) <= MIN_SPLIT_CHUNK_LINES:
        return None
    midpoint = len(chunk.lines) // 2
    first = SourceChunk(
        start_line=chunk.start_line,
        end_line=chunk.start_line + midpoint - 1,
        lines=chunk.lines[:midpoint],
    )
    second = SourceChunk(
        start_line=chunk.start_line + midpoint,
        end_line=chunk.end_line,
        lines=chunk.lines[midpoint:],
    )
    return first, second


def split_chunk_into_individual_lines(chunk: SourceChunk) -> list[SourceChunk]:
    """Last-resort, finer-grained split used only when `split_chunk_in_half` can no longer
    subdivide by line count (`chunk` is already at or below `MIN_SPLIT_CHUNK_LINES`) but the
    chunk still truncates -- e.g. a "5-line" chunk whose physical lines are each an entire
    600-1000+ character Markdown bullet (one alternate résumé summary per line, observed in the
    real corpus), where line *count* alone understates how much content still needs extracting.
    Splits into exactly one `SourceChunk` per physical line, each preserving its own original
    document line number exactly (never renumbered, never merged) -- the finest granularity this
    chunker supports.

    Returns an empty list when `chunk` is already a single line -- there is nothing finer to
    split into, and the caller must fail closed rather than split forever.
    """
    if len(chunk.lines) <= 1:
        return []
    return [
        SourceChunk(start_line=chunk.start_line + i, end_line=chunk.start_line + i, lines=(line,))
        for i, line in enumerate(chunk.lines)
    ]


_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?]+(?=\s|$)")


def split_line_into_sentences(chunk: SourceChunk) -> list[SourceChunk]:
    """Deterministic, LLM-free last-resort split (Candidate Memory recovery, 2026-09-03) for a
    single-line chunk that still truncates even at `split_chunk_into_individual_lines`'s finest
    granularity -- e.g. one physical Markdown bullet containing several complete sentences, none
    of which alone would truncate. No provider call is ever involved in deciding *how* to split;
    only sentence-ending punctuation (`.`, `!`, `?`, followed by whitespace or end of line) is
    used as a boundary, and each resulting fragment is an *exact*, unmodified slice of the
    original line text -- never re-wrapped, trimmed beyond the split point, or rewritten -- paired
    with its precise `start_char`/`end_char` offsets into that line (`SourceChunk.start_char`/
    `end_char`).

    Only applies to a chunk that is itself exactly one whole physical line and not already a
    sentence-level fragment (`chunk.start_char is None`) -- anything else returns `[]` immediately,
    since there is no finer, still-trustworthy granularity below a sentence: a single sentence that
    itself still truncates is a genuine content-density limit, not something this function can
    subdivide further.

    Fails closed (returns `[]`) whenever the located boundaries cannot be trusted to exactly
    reconstruct the original line: fewer than two non-empty fragments, the first fragment not
    starting at character 0, the last fragment not ending at the line's exact length, or any
    fragment overlapping/preceding the one before it. The caller must then treat this line as
    unresolved rather than silently dropping, merging, or normalizing any of its content.
    """
    if chunk.start_line != chunk.end_line or len(chunk.lines) != 1 or chunk.start_char is not None:
        return []

    line = chunk.lines[0]
    boundaries = [match.end() for match in _SENTENCE_BOUNDARY_RE.finditer(line)]
    if not boundaries or boundaries[-1] != len(line):
        boundaries.append(len(line))

    fragments: list[tuple[int, int]] = []
    cursor = 0
    for end in boundaries:
        start = cursor
        while start < end and line[start].isspace():
            start += 1  # skip the inter-sentence whitespace itself; never included in a fragment
        if start < end:
            fragments.append((start, end))
        cursor = end

    if len(fragments) < 2:
        return []
    if fragments[0][0] != 0 or fragments[-1][1] != len(line):
        return []
    for (_prev_start, prev_end), (start, _end) in zip(fragments, fragments[1:]):
        if start < prev_end:
            return []

    return [
        SourceChunk(
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            lines=(line[start:end],),
            start_char=start,
            end_char=end,
        )
        for start, end in fragments
    ]


def render_chunk_for_prompt(chunk: SourceChunk) -> str:
    """Render a chunk with each line prefixed by its *original* document line number, so the
    model can report back accurate start_line/end_line values in its structured output."""
    return "\n".join(f"{chunk.start_line + i}: {line}" for i, line in enumerate(chunk.lines))
