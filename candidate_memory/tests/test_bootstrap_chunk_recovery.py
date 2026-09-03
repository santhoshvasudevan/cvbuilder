"""Candidate Memory recovery (2026-09-03): bounded recursive chunk-splitting on a
finish_reason=length truncation, the durable per-chunk ChunkExtractionAttempt trail, the fix to
coarse duplicate-grouping, and the force-reextract recovery path."""

from __future__ import annotations

import hashlib
from unittest import mock

from django.test import SimpleTestCase, TestCase

from llm_provider.errors import LLMErrorCategory, NormalizedLLMError
from llm_provider.types import NormalizedLLMResult, TokenUsage

from ..models import CandidateMemory, ChunkExtractionAttempt, MemoryClaim, MemorySourceDocument
from ..schemas import ChunkExtractionResult, ContentPlane, ExtractedItem, SourcePassage
from ..services import bootstrap as bootstrap_service
from ..services.bootstrap import ReadSource
from ..services.chunking import SourceChunk, split_chunk_in_half, split_chunk_into_individual_lines
from ..services.storage import store_extracted_item
from .factories import make_revision, make_source, scripted_extraction


def _truncation_result():
    return NormalizedLLMResult(
        error=NormalizedLLMError(
            category=LLMErrorCategory.CONFIGURATION,
            message="Provider truncated output at the configured token limit before producing "
            "valid content (finish_reason=length). Raise max_output_tokens or reduce reasoning.",
        ),
        usage=TokenUsage(input_tokens=10, output_tokens=4096, total_tokens=4106),
    )


def _success_result(items=None):
    return NormalizedLLMResult(
        content=ChunkExtractionResult(items=items or []),
        usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


class SplitChunkInHalfTests(SimpleTestCase):
    def test_splits_preserve_original_line_numbers(self):
        chunk = SourceChunk(start_line=10, end_line=29, lines=tuple(f"line{i}" for i in range(20)))
        first, second = split_chunk_in_half(chunk)
        self.assertEqual((first.start_line, first.end_line), (10, 19))
        self.assertEqual((second.start_line, second.end_line), (20, 29))
        self.assertEqual(first.lines + second.lines, chunk.lines)

    def test_returns_none_at_or_below_minimum_size(self):
        chunk = SourceChunk(start_line=1, end_line=5, lines=tuple(f"line{i}" for i in range(5)))
        self.assertIsNone(split_chunk_in_half(chunk))


class SplitChunkIntoIndividualLinesTests(SimpleTestCase):
    def test_splits_into_one_chunk_per_physical_line_preserving_line_numbers(self):
        chunk = SourceChunk(start_line=32, end_line=36, lines=("a", "b", "c", "d", "e"))
        result = split_chunk_into_individual_lines(chunk)
        self.assertEqual(len(result), 5)
        self.assertEqual([(c.start_line, c.end_line, c.lines) for c in result], [
            (32, 32, ("a",)), (33, 33, ("b",)), (34, 34, ("c",)),
            (35, 35, ("d",)), (36, 36, ("e",)),
        ])

    def test_returns_empty_list_for_a_single_line_chunk(self):
        chunk = SourceChunk(start_line=1, end_line=1, lines=("only line",))
        self.assertEqual(split_chunk_into_individual_lines(chunk), [])


class RecursiveSplitOnTruncationTests(TestCase):
    def _chunk(self, n_lines: int, start_line: int = 1) -> SourceChunk:
        return SourceChunk(
            start_line=start_line, end_line=start_line + n_lines - 1,
            lines=tuple(f"line {i}" for i in range(n_lines)),
        )

    def _run(self, chunk, side_effect):
        rev = make_revision()
        source = make_source(rev, raw_content="\n".join(chunk.lines) + "\n")
        build_summary = {"extraction_errors": 0, "claims_extracted": 0, "rules_extracted": 0}
        with mock.patch("candidate_memory.services.bootstrap.extract_chunk", side_effect=side_effect):
            bootstrap_service._process_chunk_with_recovery(
                chunk, source_role="ENGLISH_CORPUS", language="en",
                source_document=source, new_revision=rev, build_summary=build_summary,
            )
        return rev, build_summary

    def test_truncation_causes_bounded_recursive_splitting_until_success(self):
        chunk = self._chunk(20)

        def side_effect(chunk_arg, **kwargs):
            return _truncation_result() if len(chunk_arg.lines) > 5 else _success_result()

        rev, build_summary = self._run(chunk, side_effect)

        # 20 -> 10+10 (both truncate) -> 5+5+5+5 (all succeed): 1 + 2 SUPERSEDED, 4 SUCCESS.
        attempts = list(ChunkExtractionAttempt.objects.filter(candidate_memory=rev))
        self.assertEqual(len(attempts), 7)
        self.assertEqual(
            sum(1 for a in attempts if a.status == ChunkExtractionAttempt.Status.SUPERSEDED), 3
        )
        self.assertEqual(
            sum(1 for a in attempts if a.status == ChunkExtractionAttempt.Status.SUCCESS), 4
        )
        self.assertEqual(build_summary["extraction_errors"], 0)

    def test_minimum_size_chunk_falls_back_to_individual_line_split(self):
        """Recovery (2026-09-03): a chunk already at MIN_SPLIT_CHUNK_LINES (5) that still
        truncates -- e.g. 5 physical lines that are each their own long Markdown bullet, as
        observed in the real corpus -- falls back to one-line-per-chunk granularity instead of
        failing closed immediately."""
        chunk = self._chunk(5)  # already at the line-count floor

        def side_effect(chunk_arg, **kwargs):
            return _truncation_result() if len(chunk_arg.lines) > 1 else _success_result()

        rev, build_summary = self._run(chunk, side_effect)

        attempts = list(ChunkExtractionAttempt.objects.filter(candidate_memory=rev))
        # 1 SUPERSEDED (the original 5-line attempt) + 5 SUCCESS (one per individual line).
        self.assertEqual(len(attempts), 6)
        self.assertEqual(
            sum(1 for a in attempts if a.status == ChunkExtractionAttempt.Status.SUPERSEDED), 1
        )
        success_attempts = [a for a in attempts if a.status == ChunkExtractionAttempt.Status.SUCCESS]
        self.assertEqual(len(success_attempts), 5)
        self.assertTrue(all(a.start_line == a.end_line for a in success_attempts))
        self.assertEqual(build_summary["extraction_errors"], 0)

    def test_fails_closed_when_minimum_size_chunk_still_truncates(self):
        chunk = self._chunk(20)
        rev, build_summary = self._run(chunk, lambda chunk_arg, **kwargs: _truncation_result())

        failed = ChunkExtractionAttempt.objects.filter(
            candidate_memory=rev, status=ChunkExtractionAttempt.Status.FAILED
        )
        self.assertTrue(failed.exists())
        self.assertGreater(build_summary["extraction_errors"], 0)
        # Every FAILED row must be a single physical line -- the finest granularity the recovery
        # falls back to (individual-line split) once MIN_SPLIT_CHUNK_LINES is reached and the
        # chunk still truncates; nothing coarser is ever left as the final, unresolved state.
        for a in failed:
            self.assertEqual(a.end_line, a.start_line)

    def test_only_failed_chunks_are_split_never_a_healthy_one(self):
        chunk = self._chunk(20)
        call_count = {"n": 0}

        def side_effect(chunk_arg, **kwargs):
            call_count["n"] += 1
            return _success_result()

        rev, build_summary = self._run(chunk, side_effect)
        self.assertEqual(call_count["n"], 1)
        attempts = ChunkExtractionAttempt.objects.filter(candidate_memory=rev)
        self.assertEqual(attempts.count(), 1)
        self.assertEqual(attempts.get().status, ChunkExtractionAttempt.Status.SUCCESS)

    def test_successful_sub_chunks_after_a_split_are_not_duplicated(self):
        """A truncated response never has any parseable content, so nothing is ever stored from
        the chunk that gets split -- only the two successful sub-chunks' own items are stored,
        exactly once each."""
        lines = tuple(f"Fact number {i}." for i in range(10))
        chunk = SourceChunk(start_line=1, end_line=10, lines=lines)
        rev = make_revision()
        source = make_source(rev, raw_content="\n".join(lines) + "\n")
        build_summary = {"extraction_errors": 0, "claims_extracted": 0, "rules_extracted": 0}

        def _item_for(line_no, text):
            return ExtractedItem(
                plane=ContentPlane.EVIDENCE,
                canonical_text_en=text,
                support=SourcePassage(quote=text, start_line=line_no, end_line=line_no, language="en"),
                claim_type="skill",
                subject_scope=f"skill:fact{line_no}",
                resume_eligible=True,
            )

        def side_effect(chunk_arg, **kwargs):
            if len(chunk_arg.lines) > 5:
                return _truncation_result()
            items = [
                _item_for(chunk_arg.start_line + i, chunk_arg.lines[i])
                for i in range(len(chunk_arg.lines))
            ]
            return _success_result(items)

        with mock.patch("candidate_memory.services.bootstrap.extract_chunk", side_effect=side_effect):
            bootstrap_service._process_chunk_with_recovery(
                chunk, source_role="ENGLISH_CORPUS", language="en",
                source_document=source, new_revision=rev, build_summary=build_summary,
            )

        self.assertEqual(build_summary["claims_extracted"], 10)
        self.assertEqual(MemoryClaim.objects.filter(candidate_memory=rev).count(), 10)


class DuplicateGroupingFixTests(TestCase):
    """Candidate Memory recovery (2026-09-03): distinct facts sharing subject_scope+claim_type
    must never be silently merged -- only an explicit duplicate_group_hint may merge two items."""

    def _item(self, text, **overrides):
        defaults = dict(
            plane=ContentPlane.EVIDENCE,
            canonical_text_en=text,
            support=SourcePassage(quote=text, start_line=1, end_line=1, language="en"),
            claim_type="responsibility",
            subject_scope="career",
            resume_eligible=True,
        )
        defaults.update(overrides)
        return ExtractedItem(**defaults)

    def test_distinct_responsibilities_sharing_scope_and_type_remain_separate(self):
        rev = make_revision()
        source = make_source(rev, raw_content="Led the migration.\nOwned the on-call rotation.\n")
        item_a = self._item("Led the migration.", support=SourcePassage(
            quote="Led the migration.", start_line=1, end_line=1, language="en"
        ))
        item_b = self._item("Owned the on-call rotation.", support=SourcePassage(
            quote="Owned the on-call rotation.", start_line=2, end_line=2, language="en"
        ))
        store_extracted_item(item_a, candidate_memory=rev, source_document=source)
        store_extracted_item(item_b, candidate_memory=rev, source_document=source)
        self.assertEqual(MemoryClaim.objects.filter(candidate_memory=rev).count(), 2)

    def test_distinct_certifications_sharing_scope_and_type_remain_separate(self):
        rev = make_revision()
        source = make_source(
            rev,
            raw_content=(
                "Google Cloud Associate Cloud Engineer.\nIBM Data Science Professional Certificate.\n"
            ),
        )
        cert_a = self._item(
            "Google Cloud Associate Cloud Engineer.", claim_type="certification",
            support=SourcePassage(
                quote="Google Cloud Associate Cloud Engineer.", start_line=1, end_line=1, language="en"
            ),
        )
        cert_b = self._item(
            "IBM Data Science Professional Certificate.", claim_type="certification",
            support=SourcePassage(
                quote="IBM Data Science Professional Certificate.", start_line=2, end_line=2, language="en"
            ),
        )
        store_extracted_item(cert_a, candidate_memory=rev, source_document=source)
        store_extracted_item(cert_b, candidate_memory=rev, source_document=source)
        self.assertEqual(MemoryClaim.objects.filter(candidate_memory=rev).count(), 2)

    def test_distinct_degrees_sharing_scope_and_type_remain_separate(self):
        rev = make_revision()
        source = make_source(rev, raw_content="M.Tech - Automotive Electronics.\nB.Tech - Electronics.\n")
        degree_a = self._item(
            "M.Tech - Automotive Electronics.", claim_type="education",
            support=SourcePassage(
                quote="M.Tech - Automotive Electronics.", start_line=1, end_line=1, language="en"
            ),
        )
        degree_b = self._item(
            "B.Tech - Electronics.", claim_type="education",
            support=SourcePassage(quote="B.Tech - Electronics.", start_line=2, end_line=2, language="en"),
        )
        store_extracted_item(degree_a, candidate_memory=rev, source_document=source)
        store_extracted_item(degree_b, candidate_memory=rev, source_document=source)
        self.assertEqual(MemoryClaim.objects.filter(candidate_memory=rev).count(), 2)

    def test_genuine_en_de_duplicates_still_merge_via_explicit_hint(self):
        rev = make_revision()
        source_en = make_source(rev, raw_content="Built the Ford integration.\n")
        source_de = make_source(
            rev, logical_source_key="german", filename="german.md", language="de", precedence=3,
            raw_content="Baute die Ford-Integration.\n",
        )
        item_en = self._item(
            "Built the Ford integration.", duplicate_group_hint="ford_integration",
            support=SourcePassage(
                quote="Built the Ford integration.", start_line=1, end_line=1, language="en"
            ),
        )
        item_de = self._item(
            "Built the Ford integration.", duplicate_group_hint="ford_integration",
            support=SourcePassage(
                quote="Baute die Ford-Integration.", start_line=1, end_line=1, language="de"
            ),
        )
        store_extracted_item(item_en, candidate_memory=rev, source_document=source_en)
        store_extracted_item(item_de, candidate_memory=rev, source_document=source_de)
        self.assertEqual(MemoryClaim.objects.filter(candidate_memory=rev).count(), 1)
        claim = MemoryClaim.objects.get(candidate_memory=rev)
        self.assertEqual(claim.supports.count(), 2)


class ForceReextractTests(TestCase):
    def _read_source(self, key, content, role=MemorySourceDocument.SourceRole.ENGLISH_CORPUS):
        return ReadSource(
            content=content,
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            logical_source_key=key,
            filename=f"{key}.md",
            source_role=role,
            language="en",
            precedence=2,
        )

    def test_force_reextract_ignores_an_existing_working_revision_without_touching_it(self):
        rev1 = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        make_source(rev1, raw_content="Employed by Malformed Corp forever.\n")
        malformed_claim = MemoryClaim.objects.create(
            candidate_memory=rev1, stable_key="malformed", canonical_text_en="Malformed claim",
            claim_type="skill", subject_scope="career", resume_eligible=True,
        )
        original_summary = dict(rev1.build_summary)

        with scripted_extraction({"items": []}):
            rev2 = bootstrap_service.build_revision_from_sources(
                [self._read_source("corpus", "Fresh content for revision 2.\n")],
                force_reextract=True,
            )

        rev1.refresh_from_db()
        self.assertEqual(rev1.status, CandidateMemory.Status.NEEDS_REVIEW)  # untouched
        self.assertEqual(rev1.build_summary, original_summary)  # untouched
        self.assertTrue(MemoryClaim.objects.filter(pk=malformed_claim.pk).exists())  # untouched
        self.assertNotEqual(rev2.pk, rev1.pk)
        self.assertIsNone(rev2.base_revision)

    def test_revision_2_does_not_inherit_revision_1s_malformed_claims(self):
        rev1 = make_revision(status=CandidateMemory.Status.NEEDS_REVIEW)
        MemoryClaim.objects.create(
            candidate_memory=rev1, stable_key="malformed", canonical_text_en="Malformed claim",
            claim_type="skill", subject_scope="career", resume_eligible=True,
        )

        with scripted_extraction({"items": []}):
            rev2 = bootstrap_service.build_revision_from_sources(
                [self._read_source("corpus", "Fresh content for revision 2.\n")],
                force_reextract=True,
            )

        self.assertEqual(MemoryClaim.objects.filter(candidate_memory=rev2).count(), 0)
        self.assertEqual(rev2.build_summary["sources_reused"], 0)
        self.assertEqual(rev2.build_summary["sources_processed"], 1)

    def test_force_reextract_without_an_existing_revision_behaves_like_a_normal_fresh_build(self):
        with scripted_extraction({"items": []}):
            rev = bootstrap_service.build_revision_from_sources(
                [self._read_source("corpus", "Some content.\n")], force_reextract=True
            )
        self.assertEqual(rev.status, CandidateMemory.Status.NEEDS_REVIEW)
        self.assertEqual(rev.build_summary["sources_processed"], 1)


class RetryFailedChunksTests(TestCase):
    """Targeted recovery (2026-09-03): retries only currently-FAILED attempts of one existing
    revision, using the exact stored source content/hash/line-range -- never a full rebuild."""

    def _make_failed_attempt(self, rev, source, *, start_line, end_line, attempt_number=1):
        return ChunkExtractionAttempt.objects.create(
            candidate_memory=rev, source_document=source,
            source_content_sha256=source.content_sha256,
            start_line=start_line, end_line=end_line,
            status=ChunkExtractionAttempt.Status.FAILED, error_category="TIMEOUT",
            attempt_number=attempt_number,
        )

    def test_duplicate_failed_rows_for_the_same_range_are_retried_only_once(self):
        """Two FAILED rows for the exact same (source, start_line, end_line) -- e.g. the original
        attempt plus a previous retry's own failed attempt -- must produce exactly one live call,
        not two; both historical rows are updated together from that one outcome."""
        rev = make_revision()
        source = make_source(rev, raw_content="\n".join(f"line {i}" for i in range(1, 11)) + "\n")
        first = self._make_failed_attempt(rev, source, start_line=1, end_line=10, attempt_number=1)
        second = self._make_failed_attempt(rev, source, start_line=1, end_line=10, attempt_number=2)

        call_count = {"n": 0}

        def side_effect(chunk_arg, **kwargs):
            call_count["n"] += 1
            return _success_result()

        with mock.patch("candidate_memory.services.bootstrap.extract_chunk", side_effect=side_effect):
            summary = bootstrap_service.retry_failed_chunks(rev, max_live_calls=40)

        self.assertEqual(call_count["n"], 1)
        self.assertEqual(summary.live_calls_made, 1)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, ChunkExtractionAttempt.Status.SUPERSEDED)
        self.assertEqual(second.status, ChunkExtractionAttempt.Status.SUPERSEDED)

    def test_retry_uses_stored_source_content_hash_and_line_range(self):
        rev = make_revision()
        source = make_source(rev, raw_content="\n".join(f"line {i}" for i in range(1, 11)) + "\n")
        self._make_failed_attempt(rev, source, start_line=3, end_line=7)

        seen_chunks = []

        def side_effect(chunk_arg, **kwargs):
            seen_chunks.append((chunk_arg.start_line, chunk_arg.end_line, chunk_arg.lines))
            return _success_result()

        with mock.patch("candidate_memory.services.bootstrap.extract_chunk", side_effect=side_effect):
            summary = bootstrap_service.retry_failed_chunks(rev, max_live_calls=40)

        self.assertEqual(summary.failed_before, 1)
        self.assertEqual(summary.recovered, 1)
        self.assertEqual(summary.still_failed, 0)
        self.assertEqual(summary.live_calls_made, 1)
        self.assertEqual(seen_chunks, [(3, 7, ("line 3", "line 4", "line 5", "line 6", "line 7"))])

    def test_recovered_attempt_marks_original_superseded_only_after_success(self):
        rev = make_revision()
        source = make_source(rev, raw_content="\n".join(f"line {i}" for i in range(1, 11)) + "\n")
        original = self._make_failed_attempt(rev, source, start_line=1, end_line=10)

        with mock.patch(
            "candidate_memory.services.bootstrap.extract_chunk",
            side_effect=lambda chunk_arg, **kwargs: _success_result(),
        ):
            bootstrap_service.retry_failed_chunks(rev, max_live_calls=40)

        original.refresh_from_db()
        self.assertEqual(original.status, ChunkExtractionAttempt.Status.SUPERSEDED)

    def test_still_failing_retry_leaves_the_original_attempt_failed_not_superseded(self):
        rev = make_revision()
        source = make_source(rev, raw_content="\n".join(f"line {i}" for i in range(1, 11)) + "\n")
        original = self._make_failed_attempt(rev, source, start_line=1, end_line=10)

        with mock.patch(
            "candidate_memory.services.bootstrap.extract_chunk",
            side_effect=lambda chunk_arg, **kwargs: _truncation_result(),
        ):
            summary = bootstrap_service.retry_failed_chunks(rev, max_live_calls=40)

        original.refresh_from_db()
        self.assertEqual(original.status, ChunkExtractionAttempt.Status.FAILED)
        self.assertEqual(summary.recovered, 0)
        self.assertEqual(summary.still_failed, 1)

    def test_live_call_budget_is_enforced_and_never_exceeded(self):
        rev = make_revision()
        source = make_source(
            rev, raw_content="\n".join(f"line {i}" for i in range(1, 51)) + "\n"
        )
        # Five *distinct* ranges -- deduplication-by-range must not collapse these into fewer
        # retries, so the budget is genuinely tested against 5 independent chunks.
        for i in range(5):
            start = i * 10 + 1
            self._make_failed_attempt(rev, source, start_line=start, end_line=start + 9)

        call_count = {"n": 0}

        def side_effect(chunk_arg, **kwargs):
            call_count["n"] += 1
            return _success_result()

        with mock.patch("candidate_memory.services.bootstrap.extract_chunk", side_effect=side_effect):
            summary = bootstrap_service.retry_failed_chunks(rev, max_live_calls=3)

        self.assertLessEqual(call_count["n"], 3)
        self.assertEqual(summary.live_calls_made, 3)
        self.assertEqual(summary.recovered, 3)
        self.assertEqual(summary.still_failed, 2)

    def test_retry_never_touches_a_different_revision(self):
        other_rev = make_revision()
        other_source = make_source(other_rev, raw_content="untouched\n")
        self._make_failed_attempt(other_rev, other_source, start_line=1, end_line=1)

        rev = make_revision()
        source = make_source(rev, raw_content="line 1\nline 2\n")
        self._make_failed_attempt(rev, source, start_line=1, end_line=1)

        with mock.patch(
            "candidate_memory.services.bootstrap.extract_chunk",
            side_effect=lambda chunk_arg, **kwargs: _success_result(),
        ):
            bootstrap_service.retry_failed_chunks(rev, max_live_calls=40)

        self.assertEqual(
            other_rev.chunk_attempts.filter(status=ChunkExtractionAttempt.Status.FAILED).count(), 1
        )


class RetryChunkLineageWithOutputOverrideTests(TestCase):
    """Last-resort single-line recovery (2026-09-03): a physical line whose every FAILED attempt,
    at any ancestor granularity, still truncates at the configured default max_output_tokens even
    once chunking has already been narrowed to that one line -- the only remaining lever is a
    higher max_output_tokens for exactly one manually authorized retry call."""

    def _make_failed_attempt(self, rev, source, *, start_line, end_line, attempt_number=1):
        return ChunkExtractionAttempt.objects.create(
            candidate_memory=rev, source_document=source,
            source_content_sha256=source.content_sha256,
            start_line=start_line, end_line=end_line,
            status=ChunkExtractionAttempt.Status.FAILED, error_category="CONFIGURATION",
            attempt_number=attempt_number,
        )

    def test_recovers_and_supersedes_every_attempt_in_the_lineage_on_success(self):
        rev = make_revision()
        source = make_source(rev, raw_content="\n".join(f"line {i}" for i in range(1, 6)) + "\n")
        # Two ancestor-granularity FAILED rows (the original 5-line chunk retried twice) plus the
        # finest-granularity single-line FAILED row -- all three describe line 3.
        ancestor_1 = self._make_failed_attempt(rev, source, start_line=1, end_line=5, attempt_number=1)
        ancestor_2 = self._make_failed_attempt(rev, source, start_line=1, end_line=5, attempt_number=2)
        leaf = self._make_failed_attempt(rev, source, start_line=3, end_line=3, attempt_number=1)

        seen_kwargs = {}

        def side_effect(chunk_arg, **kwargs):
            seen_kwargs.update(kwargs)
            return _success_result()

        with mock.patch("candidate_memory.services.bootstrap.extract_chunk", side_effect=side_effect):
            summary = bootstrap_service.retry_chunk_lineage_with_output_override(
                rev, source_document=source, line_number=3, max_output_tokens=8192
            )

        self.assertEqual(seen_kwargs["max_output_tokens_override"], 8192)
        self.assertEqual(summary.live_calls_made, 1)
        self.assertEqual(summary.failed_before, 3)
        self.assertEqual(summary.recovered, 3)
        self.assertEqual(summary.still_failed, 0)
        for attempt in (ancestor_1, ancestor_2, leaf):
            attempt.refresh_from_db()
            self.assertEqual(attempt.status, ChunkExtractionAttempt.Status.SUPERSEDED)

    def test_still_truncating_leaves_every_attempt_in_the_lineage_failed(self):
        rev = make_revision()
        source = make_source(rev, raw_content="\n".join(f"line {i}" for i in range(1, 6)) + "\n")
        leaf = self._make_failed_attempt(rev, source, start_line=3, end_line=3, attempt_number=1)

        with mock.patch(
            "candidate_memory.services.bootstrap.extract_chunk",
            side_effect=lambda chunk_arg, **kwargs: _truncation_result(),
        ):
            summary = bootstrap_service.retry_chunk_lineage_with_output_override(
                rev, source_document=source, line_number=3, max_output_tokens=8192
            )

        leaf.refresh_from_db()
        self.assertEqual(leaf.status, ChunkExtractionAttempt.Status.FAILED)
        self.assertEqual(summary.live_calls_made, 1)
        self.assertEqual(summary.recovered, 0)
        self.assertEqual(summary.still_failed, 1)

    def test_makes_at_most_one_call_even_though_the_chunk_could_theoretically_split(self):
        rev = make_revision()
        source = make_source(rev, raw_content="\n".join(f"line {i}" for i in range(1, 6)) + "\n")
        self._make_failed_attempt(rev, source, start_line=3, end_line=3, attempt_number=1)

        call_count = {"n": 0}

        def side_effect(chunk_arg, **kwargs):
            call_count["n"] += 1
            return _truncation_result()

        with mock.patch("candidate_memory.services.bootstrap.extract_chunk", side_effect=side_effect):
            bootstrap_service.retry_chunk_lineage_with_output_override(
                rev, source_document=source, line_number=3, max_output_tokens=8192
            )

        self.assertEqual(call_count["n"], 1)

    def test_fails_closed_when_source_content_has_changed(self):
        rev = make_revision()
        source = make_source(rev, raw_content="\n".join(f"line {i}" for i in range(1, 6)) + "\n")
        leaf = self._make_failed_attempt(rev, source, start_line=3, end_line=3, attempt_number=1)
        leaf.source_content_sha256 = "stale-hash-does-not-match-current-source"
        leaf.save()

        with mock.patch("candidate_memory.services.bootstrap.extract_chunk") as extract_mock:
            summary = bootstrap_service.retry_chunk_lineage_with_output_override(
                rev, source_document=source, line_number=3, max_output_tokens=8192
            )

        extract_mock.assert_not_called()
        leaf.refresh_from_db()
        self.assertEqual(leaf.status, ChunkExtractionAttempt.Status.FAILED)
        self.assertEqual(summary.live_calls_made, 0)
        self.assertEqual(summary.still_failed, 1)

    def test_no_matching_failed_attempts_is_a_true_no_op(self):
        rev = make_revision()
        source = make_source(rev, raw_content="\n".join(f"line {i}" for i in range(1, 6)) + "\n")

        with mock.patch("candidate_memory.services.bootstrap.extract_chunk") as extract_mock:
            summary = bootstrap_service.retry_chunk_lineage_with_output_override(
                rev, source_document=source, line_number=3, max_output_tokens=8192
            )

        extract_mock.assert_not_called()
        self.assertEqual(summary.failed_before, 0)
        self.assertEqual(summary.live_calls_made, 0)

    def test_never_touches_a_different_revision(self):
        other_rev = make_revision()
        other_source = make_source(other_rev, raw_content="untouched\n")
        other_leaf = self._make_failed_attempt(other_rev, other_source, start_line=1, end_line=1)

        rev = make_revision()
        source = make_source(rev, raw_content="\n".join(f"line {i}" for i in range(1, 6)) + "\n")
        self._make_failed_attempt(rev, source, start_line=3, end_line=3, attempt_number=1)

        with mock.patch(
            "candidate_memory.services.bootstrap.extract_chunk",
            side_effect=lambda chunk_arg, **kwargs: _success_result(),
        ):
            bootstrap_service.retry_chunk_lineage_with_output_override(
                rev, source_document=source, line_number=3, max_output_tokens=8192
            )

        other_leaf.refresh_from_db()
        self.assertEqual(other_leaf.status, ChunkExtractionAttempt.Status.FAILED)
