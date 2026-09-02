"""Bounded, line-number-preserving chunking -- never send a full document in one request, and
never let one chunk grow unboundedly large just because its lines happen to be long (audit
repair: character-bound in addition to the original line-count bound)."""

from __future__ import annotations

from django.test import SimpleTestCase

from ..services.chunking import chunk_source, render_chunk_for_prompt


class ChunkingTests(SimpleTestCase):
    def test_short_document_is_one_chunk(self):
        content = "\n".join(f"line {i}" for i in range(1, 11))
        chunks = chunk_source(content, chunk_lines=80)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].start_line, 1)
        self.assertEqual(chunks[0].end_line, 10)

    def test_long_document_is_bounded_into_multiple_chunks(self):
        content = "\n".join(f"line {i}" for i in range(1, 201))
        chunks = chunk_source(content, chunk_lines=80)
        self.assertEqual(len(chunks), 3)
        self.assertEqual([c.start_line for c in chunks], [1, 81, 161])
        self.assertEqual([c.end_line for c in chunks], [80, 160, 200])

    def test_chunk_lines_preserve_original_line_numbering_in_prompt_render(self):
        content = "\n".join(f"line {i}" for i in range(1, 201))
        chunks = chunk_source(content, chunk_lines=80)
        rendered = render_chunk_for_prompt(chunks[1])
        self.assertTrue(rendered.startswith("81: line 81"))
        self.assertIn("160: line 160", rendered)
        self.assertNotIn("1: line 1\n", rendered)

    def test_empty_document_has_no_chunks(self):
        self.assertEqual(chunk_source(""), [])

    def test_no_chunk_ever_exceeds_the_character_bound(self):
        content = "\n".join(f"line {i}: some moderate length text here" for i in range(1, 500))
        chunks = chunk_source(content, chunk_lines=1000, max_chunk_chars=500)
        for chunk in chunks:
            rendered = render_chunk_for_prompt(chunk)
            self.assertLessEqual(len(rendered), 600)  # bound + line-number-prefix overhead

    def test_no_empty_chunks_are_generated(self):
        content = "line 1\nline 2\n"
        chunks = chunk_source(content, chunk_lines=1, max_chunk_chars=1)
        for chunk in chunks:
            self.assertGreater(len(chunk.lines), 0)
            self.assertTrue(any(chunk.lines))

    def test_one_extremely_long_line_is_safely_split_with_valid_provenance(self):
        long_line = "x" * 25_000
        content = f"short line\n{long_line}\nanother short line"
        chunks = chunk_source(content, chunk_lines=80, max_chunk_chars=8000)

        # The long line (line 2) must be split into multiple fragments, each still tagged line 2.
        line_2_chunks = [c for c in chunks if c.start_line == 2 and c.end_line == 2]
        self.assertGreater(len(line_2_chunks), 1)
        for c in line_2_chunks:
            self.assertLessEqual(len(c.lines[0]), 8000)
        # Reassembling every fragment's text recovers the original line exactly.
        reassembled = "".join(c.lines[0] for c in line_2_chunks)
        self.assertEqual(reassembled, long_line)
        # No chunk is empty.
        for c in chunks:
            self.assertTrue(c.lines)
            self.assertTrue(all(c.lines))

    def test_multibyte_unicode_content_is_chunked_without_corruption(self):
        # German umlauts and other multibyte characters must round-trip exactly.
        content = "\n".join(
            ["Müller: Straße, größer, Prüfung.", "日本語のテキスト行です。", "Emoji test: 🎉🚀✅"]
        )
        chunks = chunk_source(content, chunk_lines=1, max_chunk_chars=100)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0].lines[0], "Müller: Straße, größer, Prüfung.")
        self.assertEqual(chunks[1].lines[0], "日本語のテキスト行です。")
        self.assertEqual(chunks[2].lines[0], "Emoji test: 🎉🚀✅")

    def test_normal_markdown_sections_chunk_and_preserve_exact_line_ranges(self):
        content = "\n".join(
            [
                "# Heading",
                "",
                "Some paragraph text.",
                "",
                "## Subheading",
                "",
                "- bullet one",
                "- bullet two",
            ]
        )
        chunks = chunk_source(content, chunk_lines=3, max_chunk_chars=8000)
        self.assertEqual(len(chunks), 3)
        self.assertEqual((chunks[0].start_line, chunks[0].end_line), (1, 3))
        self.assertEqual((chunks[1].start_line, chunks[1].end_line), (4, 6))
        self.assertEqual((chunks[2].start_line, chunks[2].end_line), (7, 8))
        self.assertEqual(chunks[2].lines, ("- bullet one", "- bullet two"))
