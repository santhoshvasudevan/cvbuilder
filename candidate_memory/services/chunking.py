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

DEFAULT_CHUNK_LINES = 80
DEFAULT_MAX_CHUNK_CHARS = 8000


@dataclasses.dataclass(frozen=True)
class SourceChunk:
    start_line: int  # 1-based, inclusive, relative to the original document
    end_line: int  # 1-based, inclusive
    lines: tuple[str, ...]  # normally one entry per line start_line..end_line; see module docstring
    # for the single-fragment-of-one-long-line case (start_line == end_line, len(lines) == 1).


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


def render_chunk_for_prompt(chunk: SourceChunk) -> str:
    """Render a chunk with each line prefixed by its *original* document line number, so the
    model can report back accurate start_line/end_line values in its structured output."""
    return "\n".join(f"{chunk.start_line + i}: {line}" for i, line in enumerate(chunk.lines))
