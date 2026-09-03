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
        "screening_risks": ["No mention of visa sponsorship."],
    }
    response.update(overrides)
    return response
