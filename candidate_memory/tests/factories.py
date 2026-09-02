"""Test-only helpers. No network, no live credentials, no cassettes -- every LLM call in this
test suite is routed to the M2 `FakeAdapter` with a scripted response, per M3's explicit
constraint that only the fake adapter may be used in automated tests.
"""

from __future__ import annotations

import contextlib
from unittest import mock

from llm_provider.adapters.fake import FakeAdapter
from llm_provider.models import LLMModel, LLMProvider, StageModelAssignment

from ..models import CandidateMemory, MemoryClaim, MemorySourceDocument


def make_source(
    candidate_memory: CandidateMemory,
    *,
    logical_source_key="source",
    filename="source.md",
    source_role=MemorySourceDocument.SourceRole.ENGLISH_CORPUS,
    language="en",
    trust_status=MemorySourceDocument.TrustStatus.OPERATOR_APPROVED,
    precedence=2,
    raw_content="line1\n",
) -> MemorySourceDocument:
    return MemorySourceDocument.objects.create(
        candidate_memory=candidate_memory,
        logical_source_key=logical_source_key,
        filename=filename,
        source_role=source_role,
        language=language,
        trust_status=trust_status,
        precedence=precedence,
        raw_content=raw_content,
    )


def make_revision(
    status=CandidateMemory.Status.BUILDING, version=None, base_revision=None
) -> CandidateMemory:
    if version is None:
        latest = CandidateMemory.objects.order_by("-version").values_list("version", flat=True).first()
        version = (latest or 0) + 1
    return CandidateMemory.objects.create(version=version, status=status, base_revision=base_revision)


def freeze_revision(revision: CandidateMemory, status: str) -> CandidateMemory:
    """Transition an existing (typically BUILDING) revision to `status` -- use this to build
    fixture content (claims/sources/conflicts) *before* freezing a revision to ACTIVE/SUPERSEDED,
    since content can only ever be created while the owning revision is mutable."""
    revision.status = status
    revision.save()
    revision.refresh_from_db()
    return revision


def make_claim(candidate_memory: CandidateMemory, **kwargs) -> MemoryClaim:
    defaults = dict(
        stable_key=f"claim_{candidate_memory.pk}_{MemoryClaim.objects.filter(candidate_memory=candidate_memory).count()}",
        canonical_text_en="A claim.",
        claim_type="skill",
        subject_scope="Some Employer",
        resume_eligible=True,
        confirmation_status=MemoryClaim.ConfirmationStatus.UNCONFIRMED,
    )
    defaults.update(kwargs)
    return MemoryClaim.objects.create(candidate_memory=candidate_memory, **defaults)


def make_fake_stage_assignment(stage=StageModelAssignment.Stage.MEMORY_BUILD) -> LLMModel:
    """Idempotent: `scripted_extraction` may be entered multiple times within one test (e.g. to
    simulate a bootstrap re-run), and the registry rows should simply be reused, not duplicated."""
    provider, _ = LLMProvider.objects.get_or_create(
        name="Fake Provider",
        defaults={"provider_type": LLMProvider.ProviderType.FAKE, "credential_env_var": "FAKE_KEY"},
    )
    model, _ = LLMModel.objects.get_or_create(
        provider=provider, model_id="fake-model", defaults={"supports_structured_output": True}
    )
    StageModelAssignment.objects.update_or_create(stage=stage, defaults={"model": model})
    return model


@contextlib.contextmanager
def scripted_extraction(fixed_response: dict):
    """Patch `candidate_memory.services.extraction.get_adapter_for_stage` so every chunk-level
    extraction call in the wrapped block returns `fixed_response`, via the real `FakeAdapter` (so
    the real retry/schema-validation/audit-log path in `BaseLLMAdapter.generate()` still runs --
    only the network call itself is replaced)."""
    model = make_fake_stage_assignment()

    def _get_adapter_for_stage(stage):
        return FakeAdapter(model, fixed_response=fixed_response)

    with mock.patch("candidate_memory.services.extraction.get_adapter_for_stage", _get_adapter_for_stage):
        yield
