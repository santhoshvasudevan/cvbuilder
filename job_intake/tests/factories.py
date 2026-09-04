"""Test-only helpers. Every LLM call in this suite is routed to the M2 `FakeAdapter` with a
scripted response -- zero network, zero live credentials, mirroring candidate_memory's own test
factories pattern."""

from __future__ import annotations

import contextlib
from unittest import mock

from llm_provider.adapters.fake import FakeAdapter
from llm_provider.models import LLMModel, LLMProvider, StageModelAssignment


def make_fake_stage_assignment(stage=StageModelAssignment.Stage.AJ_ANALYZE) -> LLMModel:
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
def scripted_analysis(fixed_response: dict):
    """Patch `job_intake.services.analyze.get_adapter_for_stage` so the AJ analysis call in the
    wrapped block returns `fixed_response`, via the real `FakeAdapter` (the real retry/schema-
    validation/audit-log path in `BaseLLMAdapter.generate()` still runs -- only the network call
    itself is replaced)."""
    model = make_fake_stage_assignment()

    def _get_adapter_for_stage(stage):
        return FakeAdapter(model, fixed_response=fixed_response)

    with mock.patch("job_intake.services.analyze.get_adapter_for_stage", _get_adapter_for_stage):
        yield


# A realistic, substantive (>=300 char) posting whose exact phrasing backs every
# `source_context` value in `valid_analysis_response()` below (2026-09-04 AJ hardening, D-022's
# provenance check requires an exact substring match, never a paraphrase) -- the default pasted
# text most `run_intake`/`rerun_analysis` tests should pair with `valid_analysis_response()`.
DEFAULT_POSTING_TEXT = (
    "Senior Backend Engineer at Globex Corporation, based in Springfield, Testland (Remote). "
    "Must have 5+ years of Python experience. Django experience is a plus. You will own the "
    "payments service end to end. This role requires close collaboration with the platform team "
    "to keep the service reliable and secure under sustained transaction volume. Candidates must "
    "be authorized to work in Testland without visa sponsorship."
)


def valid_analysis_response(**overrides) -> dict:
    response = {
        "employer": "Globex Corporation",
        "role_title": "Senior Backend Engineer",
        "posting_language": "en",
        "location": "Springfield, Testland",
        "work_arrangement": "Remote",
        "requirements": [
            {
                "category": "MANDATORY",
                "text": "5+ years of Python experience",
                "source_context": "Must have 5+ years of Python experience.",
            },
            {
                "category": "PREFERRED",
                "text": "Experience with Django",
                "source_context": "Django experience is a plus.",
            },
            {
                "category": "RESPONSIBILITY",
                "text": "Own the payments service",
                "source_context": "You will own the payments service end to end.",
            },
            {
                "category": "ATS_SIGNAL",
                "text": "Python",
                "source_context": "",
            },
            {
                "category": "IMPLIED_EXPECTATION",
                "text": "Comfortable working with minimal oversight in a small team",
                "source_context": "",
            },
        ],
        "screening_risks": [
            {
                "text": "Candidates must be authorized to work in Testland without visa sponsorship.",
                "source_context": (
                    "Candidates must be authorized to work in Testland without visa sponsorship."
                ),
            }
        ],
    }
    response.update(overrides)
    return response
