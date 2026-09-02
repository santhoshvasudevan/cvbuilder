"""Explicit, operator-invoked Candidate Memory bootstrap (D-015).

    python manage.py bootstrap_candidate_memory \\
        --primary docs/AC/AC-MEMORY_PROFILE.md \\
        --english docs/AC/AC-profile_english.md \\
        --german docs/AC/AC-profile_german.md \\
        --operator-update docs/AC/AC-OPERATOR_FACT_RESOLUTIONS.md

Never run automatically -- not on migrate, not on runserver, not in tests, not on deploy. Always
leaves the new revision in NEEDS_REVIEW; activation is a separate, explicit operator action
(`manage.py` has no "activate" command by design -- that happens through the Candidate Memory UI
or `services.lifecycle.activate_revision` directly, so the operator always reviews first).
"""

from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from candidate_memory.exceptions import ExistingWorkingRevisionError
from candidate_memory.models import CandidateMemory, MemorySourceDocument
from candidate_memory.services.bootstrap import SourceSpec, build_revision
from candidate_memory.services.preflight import build_preflight_report

# Precedence convention (lower = higher precedence), shared with services/conflicts.py.
_PRECEDENCE = {
    MemorySourceDocument.SourceRole.OPERATOR_UPDATE: 0,
    MemorySourceDocument.SourceRole.PRIMARY_PROFILE: 1,
    MemorySourceDocument.SourceRole.ENGLISH_CORPUS: 2,
    MemorySourceDocument.SourceRole.GERMAN_CORPUS: 3,
}


class Command(BaseCommand):
    help = "Explicit, repeatable Candidate Memory bootstrap from named source files. Never run automatically."

    def add_arguments(self, parser):
        parser.add_argument("--primary", required=True, help="Path to the primary curated profile.")
        parser.add_argument(
            "--english", required=True, help="Path to the English evidence/expression corpus."
        )
        parser.add_argument("--german", required=True, help="Path to the German evidence/expression corpus.")
        parser.add_argument(
            "--operator-update",
            action="append",
            default=[],
            dest="operator_updates",
            help="Path to a dated operator-resolution file. May be given multiple times.",
        )
        parser.add_argument(
            "--abandon-existing",
            action="store_true",
            default=False,
            help=(
                "If a BUILDING/NEEDS_REVIEW working revision already exists, mark it FAILED and "
                "proceed with a new build, instead of refusing. Never touches the ACTIVE revision."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help=(
                "Report what a real build would do -- resolved sources, hashes, chunk/call "
                "estimate, credential/model configuration -- with zero provider calls and zero "
                "database writes. Never prints source excerpts or candidate content."
            ),
        )

    def _print_dry_run(self, specs: list[SourceSpec]) -> None:
        report = build_preflight_report(specs)
        self.stdout.write(self.style.WARNING("DRY RUN -- no provider call, no database write."))
        self.stdout.write(
            f"Existing working revision: {report.existing_working_revision or 'none'}"
        )
        self.stdout.write("Sources:")
        for source in report.sources:
            reuse = "REUSE (unchanged)" if source.would_reuse_unchanged else "PROCESS"
            self.stdout.write(
                f"  - {source.filename} [{source.source_role}] key={source.logical_source_key} "
                f"sha256={source.content_sha256[:16]}... chars={source.char_count} "
                f"lines={source.line_count} chunks={source.chunk_count} -> {reuse}"
            )
        self.stdout.write(f"Total planned chunks / estimated provider calls: {report.total_chunks}")
        self.stdout.write(
            "Estimated input tokens (approximate, ~4 chars/token heuristic, NOT a provider "
            f"billing figure): {report.estimated_input_tokens}"
        )
        if report.stage_configured:
            self.stdout.write(
                f"MEMORY_BUILD stage assigned to: {report.provider_name} "
                f"({report.provider_type}) / {report.model_id}"
            )
            self.stdout.write(
                f"Credential env var {report.credential_env_var!r} configured: "
                f"{report.credential_configured} (value never printed)"
            )
        else:
            self.stdout.write(self.style.WARNING("MEMORY_BUILD stage has no StageModelAssignment."))
        self.stdout.write(
            f"Configured max output tokens per call: {report.configured_max_output_tokens} "
            "(registry LLMModel.max_output_tokens if set, else the conservative canary default)"
        )
        self.stdout.write(
            "Maximum theoretical output tokens across this run "
            f"(configured limit x planned calls): {report.max_theoretical_output_tokens}"
        )
        if report.would_make_live_call:
            self.stdout.write(
                self.style.WARNING(
                    "Without --dry-run, this command WOULD make live provider call(s)."
                )
            )
        else:
            self.stdout.write("Without --dry-run, this command would NOT make a live provider call.")

    def handle(self, *args, **options):
        specs = [
            SourceSpec(
                path=Path(options["primary"]),
                logical_source_key="primary_profile",
                source_role=MemorySourceDocument.SourceRole.PRIMARY_PROFILE,
                language="en",
                precedence=_PRECEDENCE[MemorySourceDocument.SourceRole.PRIMARY_PROFILE],
            ),
            SourceSpec(
                path=Path(options["english"]),
                logical_source_key="english_corpus",
                source_role=MemorySourceDocument.SourceRole.ENGLISH_CORPUS,
                language="en",
                precedence=_PRECEDENCE[MemorySourceDocument.SourceRole.ENGLISH_CORPUS],
            ),
            SourceSpec(
                path=Path(options["german"]),
                logical_source_key="german_corpus",
                source_role=MemorySourceDocument.SourceRole.GERMAN_CORPUS,
                language="de",
                precedence=_PRECEDENCE[MemorySourceDocument.SourceRole.GERMAN_CORPUS],
            ),
        ]
        for update_path in options["operator_updates"]:
            path = Path(update_path)
            specs.append(
                SourceSpec(
                    path=path,
                    logical_source_key=f"operator_update__{path.stem}",
                    source_role=MemorySourceDocument.SourceRole.OPERATOR_UPDATE,
                    language="en",
                    precedence=_PRECEDENCE[MemorySourceDocument.SourceRole.OPERATOR_UPDATE],
                )
            )

        for spec in specs:
            if not spec.path.exists():
                raise CommandError(f"Source file does not exist: {spec.path}")

        if options["dry_run"]:
            self._print_dry_run(specs)
            return

        try:
            revision = build_revision(specs, abandon_existing=options["abandon_existing"])
        except ExistingWorkingRevisionError as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            failed = (
                CandidateMemory.objects.filter(status=CandidateMemory.Status.FAILED)
                .order_by("-version")
                .first()
            )
            if failed is not None:
                raise CommandError(
                    f"Build failed and CandidateMemory v{failed.version} was marked FAILED "
                    f"(never left looking review-ready). Reason: {exc}. Build summary so far: "
                    f"{failed.build_summary}. The ACTIVE revision, if any, was not touched. Retry "
                    "with --abandon-existing once you've addressed the cause, or investigate first."
                ) from exc
            raise

        self.stdout.write(
            self.style.SUCCESS(f"Created CandidateMemory version {revision.version} ({revision.status}).")
        )
        self.stdout.write(f"Build summary: {revision.build_summary}")
        self.stdout.write(
            self.style.WARNING(
                "This revision is NEEDS_REVIEW, not active. Review claims and conflicts, then "
                "activate explicitly through the Candidate Memory UI."
            )
        )
