"""Read-only bootstrap preflight (audit repair): reports what a real build would do -- resolved
source identity, char/line/chunk counts, an approximate call/token estimate, and whether the
`MEMORY_BUILD` stage appears configured for a live call -- without making any provider call,
writing any `MemorySourceDocument`/`CandidateMemory`/`LLMCallLog` row, or printing any source
excerpt or candidate content. See `management/commands/bootstrap_candidate_memory.py`'s
`--dry-run` flag, which is the only caller.

Chunk counts here use the exact same `services.chunking.chunk_source()` call (same default
line/char bounds) that the real build uses, so a dry-run's reported chunk/call count is not an
approximation of a different calculation -- it is the same calculation, just not followed by an
actual provider call.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os

from llm_provider.models import LLMProvider, StageModelAssignment

from .bootstrap import SourceSpec
from .chunking import chunk_source
from .extraction import DEFAULT_MAX_OUTPUT_TOKENS
from .revision import current_active_revision, current_working_revision


@dataclasses.dataclass
class SourcePreflight:
    filename: str
    source_role: str
    logical_source_key: str
    content_sha256: str
    char_count: int
    line_count: int
    chunk_count: int
    would_reuse_unchanged: bool


@dataclasses.dataclass
class BootstrapPreflightReport:
    sources: list[SourcePreflight]
    total_chunks: int
    estimated_call_count: int
    estimated_input_tokens: int  # approximate only -- see docstring on build_preflight_report
    existing_working_revision: str | None
    stage_configured: bool
    provider_name: str | None
    provider_type: str | None
    model_id: str | None
    credential_env_var: str | None
    credential_configured: bool
    would_make_live_call: bool
    configured_max_output_tokens: int
    max_theoretical_output_tokens: int


def build_preflight_report(source_specs: list[SourceSpec]) -> BootstrapPreflightReport:
    """Pure read: touches the filesystem (to hash/count the named source files, exactly as a real
    build would) and the database (read-only queries against existing revisions/registry rows).
    Never writes anything, never calls `llm_provider.adapters.get_adapter_for_stage`.
    """
    existing_working = current_working_revision()
    existing_label = (
        f"v{existing_working.version} ({existing_working.status})" if existing_working else None
    )

    if existing_working is not None:
        old_by_key = {s.logical_source_key: s for s in existing_working.source_documents.all()}
    else:
        active = current_active_revision()
        old_by_key = {s.logical_source_key: s for s in active.source_documents.all()} if active else {}

    sources: list[SourcePreflight] = []
    total_chunks = 0
    estimated_input_tokens = 0
    for spec in source_specs:
        raw = spec.path.read_text(encoding="utf-8")
        sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        old = old_by_key.get(spec.logical_source_key)
        would_reuse = old is not None and old.content_sha256 == sha
        chunk_count = 0 if would_reuse else len(chunk_source(raw))
        total_chunks += chunk_count
        if not would_reuse:
            # Rough, provider-independent heuristic (~4 characters per token) -- an estimate for
            # preflight visibility only, never a claim about any provider's actual tokenization or
            # billing.
            estimated_input_tokens += len(raw) // 4
        line_count = raw.count("\n") + (1 if raw and not raw.endswith("\n") else 0)
        sources.append(
            SourcePreflight(
                filename=spec.path.name,
                source_role=spec.source_role,
                logical_source_key=spec.logical_source_key,
                content_sha256=sha,
                char_count=len(raw),
                line_count=line_count,
                chunk_count=chunk_count,
                would_reuse_unchanged=would_reuse,
            )
        )

    assignment = (
        StageModelAssignment.objects.filter(stage=StageModelAssignment.Stage.MEMORY_BUILD)
        .select_related("model__provider")
        .first()
    )
    stage_configured = assignment is not None
    provider_name = assignment.model.provider.name if assignment else None
    provider_type = assignment.model.provider.provider_type if assignment else None
    model_id = assignment.model.model_id if assignment else None
    credential_env_var = assignment.model.provider.credential_env_var if assignment else None
    # Only ever check *whether* the named env var has a non-empty value -- never read/log/return
    # the value itself.
    credential_configured = bool(credential_env_var) and bool(os.environ.get(credential_env_var))
    # Audit repair: the same resolution `extraction.extract_chunk` applies -- the resolved
    # model's own registry ceiling wins when set, otherwise the conservative canary default.
    # Never unbounded, and always shown before any real call is made.
    configured_max_output_tokens = (
        (assignment.model.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS)
        if assignment
        else DEFAULT_MAX_OUTPUT_TOKENS
    )
    max_theoretical_output_tokens = configured_max_output_tokens * total_chunks

    would_make_live_call = (
        existing_working is None
        and stage_configured
        and provider_type != LLMProvider.ProviderType.FAKE
        and credential_configured
        and total_chunks > 0
    )

    return BootstrapPreflightReport(
        sources=sources,
        total_chunks=total_chunks,
        estimated_call_count=total_chunks,
        estimated_input_tokens=estimated_input_tokens,
        existing_working_revision=existing_label,
        stage_configured=stage_configured,
        provider_name=provider_name,
        provider_type=provider_type,
        model_id=model_id,
        credential_env_var=credential_env_var,
        credential_configured=credential_configured,
        would_make_live_call=would_make_live_call,
        configured_max_output_tokens=configured_max_output_tokens,
        max_theoretical_output_tokens=max_theoretical_output_tokens,
    )
