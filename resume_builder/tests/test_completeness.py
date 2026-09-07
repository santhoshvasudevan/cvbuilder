"""D-037 completeness enforcement tests (`validators/completeness.py`, plus the
`MAX_BULLETS_PER_ENGAGEMENT` cap in `schemas.py`/`validators/no_fabrication.py`) -- synthetic data
only, exercising both the unit-level validator functions and the real `build_resume_draft`
orchestration end to end (FakeAdapter only).
"""

from __future__ import annotations

from django.test import TestCase

from candidate_matching.models import FitAssessment
from candidate_matching.services.baseline_chronology import build_manifest_for_job_relevant_claim_ids
from candidate_memory.models import CandidateMemory, ClaimEngagementMapping
from job_applications.models import JobApplication
from job_intake.models import JobRequirementAnalysis

from ..schemas import (
    MAX_BULLETS_PER_ENGAGEMENT,
    AgentBuilderOutput,
    ExperienceSectionItem,
    ResumeElementItem,
)
from ..services.build import ResumeBuilderError, build_resume_draft
from ..validators.completeness import (
    CompletenessError,
    check_engagement_completeness,
    check_language_completeness,
    ensure_completeness,
)
from ..validators.no_fabrication import NoFabricationError, ValidatedElement, validate_and_flatten
from .factories import (
    freeze_revision,
    make_engagement,
    make_narrative_claim,
    make_revision,
    scripted_generation,
)


def _make_jra() -> JobRequirementAnalysis:
    application = JobApplication.objects.create()
    return JobRequirementAnalysis.objects.create(
        job_application=application, version=1,
        source_type=JobRequirementAnalysis.SourceType.PASTED,
        original_input="x" * 25, extracted_text="x" * 25,
        extracted_text_sha256="0" * 64, posting_language="en",
    )


def _element(text: str, claim_ids: list[str]) -> dict:
    return {"text": text, "supporting_memory_claim_ids": claim_ids, "matched_job_requirement_ids": []}


def _pinned_fit_assessment(candidate_memory, approved_engagements, job_relevant_claim_ids):
    manifest = build_manifest_for_job_relevant_claim_ids(
        candidate_memory, list(approved_engagements), list(job_relevant_claim_ids)
    )
    return FitAssessment(
        based_on_jra=_make_jra(),
        based_on_candidate_memory=candidate_memory,
        retrieved_claim_ids=list(job_relevant_claim_ids),
        retrieved_engagement_ids=[e.engagement_id for e in approved_engagements],
        baseline_chronology_manifest=manifest,
    )


class EngagementCompletenessUnitTests(TestCase):
    def _context(self, *, no_eligible_evidence=()):
        from ..services.context import build_builder_context

        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev, canonical_text_en="x")
        ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        fit_assessment = _pinned_fit_assessment(rev, [engagement], [claim.claim_id])
        context = build_builder_context(fit_assessment)
        return context, engagement, claim

    def test_engagement_with_eligible_evidence_and_no_bullets_is_model_omitted_content(self):
        context, engagement, _claim = self._context()
        failures = check_engagement_completeness([], context)
        self.assertEqual(len(failures), 1)
        self.assertIn(engagement.engagement_id, failures[0])
        self.assertIn("MODEL_OMITTED_CONTENT", failures[0])

    def test_engagement_with_a_placed_bullet_passes(self):
        context, engagement, claim = self._context()
        elements = [
            ValidatedElement(
                section="EXPERIENCE_BULLET", engagement_id=engagement.engagement_id, order=1,
                text="Did the work.", supporting_memory_claim_ids=[claim.claim_id],
                matched_job_requirement_ids=[],
            )
        ]
        self.assertEqual(check_engagement_completeness(elements, context), [])

    def test_no_eligible_evidence_engagement_is_exempt(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        fit_assessment = _pinned_fit_assessment(rev, [engagement], [])
        from ..services.context import build_builder_context

        context = build_builder_context(fit_assessment)
        self.assertIn(engagement.engagement_id, context.engagements_without_eligible_evidence)
        self.assertEqual(check_engagement_completeness([], context), [])


class LanguageCompletenessUnitTests(TestCase):
    def test_missing_pinned_language_claim_is_reported(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        language_claim = make_narrative_claim(
            rev, claim_type="language_proficiency", subject_scope="language:german",
            canonical_text_en="German B1 confirmed.", stable_key="lang-1",
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        fit_assessment = _pinned_fit_assessment(rev, [], [])
        from ..services.context import build_builder_context

        context = build_builder_context(fit_assessment)
        self.assertIn(language_claim.claim_id, context.pinned_language_claim_ids)

        failures = check_language_completeness([], context)
        self.assertEqual(len(failures), 1)
        self.assertIn(language_claim.claim_id, failures[0])

    def test_cited_pinned_language_claim_passes(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        language_claim = make_narrative_claim(
            rev, claim_type="language_proficiency", subject_scope="language:german",
            canonical_text_en="German B1 confirmed.", stable_key="lang-1",
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        fit_assessment = _pinned_fit_assessment(rev, [], [])
        from ..services.context import build_builder_context

        context = build_builder_context(fit_assessment)
        elements = [
            ValidatedElement(
                section="LANGUAGE", engagement_id="", order=1, text="German (B1)",
                supporting_memory_claim_ids=[language_claim.claim_id], matched_job_requirement_ids=[],
            )
        ]
        self.assertEqual(check_language_completeness(elements, context), [])

    def test_ensure_completeness_raises_a_single_error_covering_all_failures(self):
        context, engagement, _claim = EngagementCompletenessUnitTests()._context()
        with self.assertRaises(CompletenessError):
            ensure_completeness([], context)


class MaxBulletsPerEngagementTests(TestCase):
    def _retrieval_and_engagement(self):
        from ..services.context import build_builder_context

        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev, canonical_text_en="x")
        ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)
        fit_assessment = _pinned_fit_assessment(rev, [engagement], [claim.claim_id])
        return build_builder_context(fit_assessment), engagement, claim

    def test_schema_rejects_more_bullets_than_the_cap(self):
        from pydantic import ValidationError

        bullets = [_element(f"Bullet {i}.", ["MC-1-0001"]) for i in range(MAX_BULLETS_PER_ENGAGEMENT + 1)]
        with self.assertRaises(ValidationError):
            AgentBuilderOutput.model_validate(
                {
                    "target_positioning": {"title_options": [], "recommended_title": "Engineer"},
                    "summary_elements": [],
                    "experience_sections": [{"engagement_id": "CE-0001", "bullets": bullets}],
                    "positioning_themes": [], "achievements": [], "skill_categories": [],
                    "selected_skills": [], "certifications": [], "languages": [],
                    "positioning_guidance": {},
                }
            )

    def test_post_response_validation_rejects_a_bypassed_schema_bound(self):
        retrieval, engagement, claim = self._retrieval_and_engagement()
        bullets = [
            ResumeElementItem(text=f"Bullet {i}.", supporting_memory_claim_ids=[claim.claim_id])
            for i in range(MAX_BULLETS_PER_ENGAGEMENT + 1)
        ]
        # ExperienceSectionItem.model_construct() bypasses pydantic validation (including the
        # schema's own bullets max_length) for this one nested section -- simulates a provider/
        # adapter path that did not enforce it, proving the defense-in-depth post-response check
        # in no_fabrication.py is what actually rejects it.
        section = ExperienceSectionItem.model_construct(
            engagement_id=engagement.engagement_id, bullets=bullets
        )
        output = AgentBuilderOutput.model_construct(
            target_positioning={"title_options": [], "recommended_title": "Engineer"},
            summary_elements=[],
            experience_sections=[section],
            positioning_themes=[], achievements=[], skill_categories=[], selected_skills=[],
            certifications=[], languages=[], positioning_guidance={"resume_language": "en"},
        )
        with self.assertRaises(NoFabricationError) as ctx:
            validate_and_flatten(output, retrieval=retrieval)
        self.assertIn("MAX_BULLETS_PER_ENGAGEMENT", str(ctx.exception))


class EndToEndCompletenessEnforcementTests(TestCase):
    """Drives the real `build_resume_draft` orchestration to prove MODEL_OMITTED_CONTENT and a
    missing pinned language fact both fail the whole build closed."""

    def _ready_application_with_language(self):
        rev = make_revision(status=CandidateMemory.Status.BUILDING)
        engagement = make_engagement()
        claim = make_narrative_claim(rev, canonical_text_en="Owned the payments service end to end.")
        language_claim = make_narrative_claim(
            rev, claim_type="language_proficiency", subject_scope="language:german",
            canonical_text_en="German B1 confirmed.", stable_key="lang-1",
        )
        ClaimEngagementMapping.objects.create(
            memory_claim=claim, career_engagement=engagement, status=ClaimEngagementMapping.Status.APPROVED
        )
        freeze_revision(rev, CandidateMemory.Status.ACTIVE)

        application = JobApplication.objects.create(pipeline_phase=JobApplication.PipelinePhase.NEW)
        jra = _make_jra_for(application)
        from job_intake.models import JobRequirement

        JobRequirement.objects.create(
            job_requirement_analysis=jra, requirement_id="JR-001", order=1, category="MANDATORY",
            text="Own the payments service end to end.",
        )
        application.advance_to_analysis(jra=jra)
        manifest = build_manifest_for_job_relevant_claim_ids(rev, [engagement], [claim.claim_id])
        fit_assessment = FitAssessment.objects.create(
            job_application=application, version=1, based_on_jra=jra, based_on_candidate_memory=rev,
            retrieved_claim_ids=[claim.claim_id], retrieved_engagement_ids=[engagement.engagement_id],
            baseline_chronology_manifest=manifest,
        )
        from candidate_matching.models import RequirementAssessment

        RequirementAssessment.objects.create(
            fit_assessment=fit_assessment, requirement_id="JR-001",
            disposition=RequirementAssessment.Disposition.MATCH,
            supporting_memory_claim_ids=[claim.claim_id],
            explanation="Directly owned an equivalent service end to end.",
        )
        application.record_fit_assessment(fit_assessment)
        application.approve_gate1()
        return application, claim.claim_id, language_claim.claim_id, engagement.engagement_id

    def test_model_omitted_content_fails_the_whole_build(self):
        application, claim_id, _language_claim_id, engagement_id = self._ready_application_with_language()
        response = {
            "target_positioning": {"title_options": [], "recommended_title": "Engineer"},
            "summary_elements": [_element("Summary.", [claim_id])],
            # No experience_sections for the one eligible engagement at all -- eligible evidence
            # existed but the model wrote nothing for it.
            "experience_sections": [],
            "positioning_themes": [], "achievements": [], "skill_categories": [],
            "selected_skills": [], "certifications": [],
            "languages": [_element("German (B1)", [_language_claim_id])],
            "positioning_guidance": {"resume_language": "en"},
        }
        with scripted_generation(response):
            with self.assertRaises(ResumeBuilderError) as ctx:
                build_resume_draft(application)
        self.assertIn("MODEL_OMITTED_CONTENT", str(ctx.exception))
        application.refresh_from_db()
        self.assertIsNone(application.current_resume_draft)

    def test_omitted_language_fact_fails_the_whole_build(self):
        application, claim_id, _language_claim_id, engagement_id = self._ready_application_with_language()
        response = {
            "target_positioning": {"title_options": [], "recommended_title": "Engineer"},
            "summary_elements": [_element("Summary.", [claim_id])],
            "experience_sections": [
                {"engagement_id": engagement_id, "bullets": [_element("Did the work.", [claim_id])]}
            ],
            "positioning_themes": [], "achievements": [], "skill_categories": [],
            "selected_skills": [], "certifications": [],
            "languages": [],  # confirmed German claim omitted entirely
            "positioning_guidance": {"resume_language": "en"},
        }
        with scripted_generation(response):
            with self.assertRaises(ResumeBuilderError) as ctx:
                build_resume_draft(application)
        self.assertIn("language", str(ctx.exception).lower())
        application.refresh_from_db()
        self.assertIsNone(application.current_resume_draft)

    def test_valid_complete_content_renders_every_engagement_and_language_fact(self):
        application, claim_id, language_claim_id, engagement_id = self._ready_application_with_language()
        response = {
            "target_positioning": {"title_options": [], "recommended_title": "Engineer"},
            "summary_elements": [_element("Summary.", [claim_id])],
            "experience_sections": [
                {"engagement_id": engagement_id, "bullets": [_element("Did the work.", [claim_id])]}
            ],
            "positioning_themes": [], "achievements": [], "skill_categories": [],
            "selected_skills": [], "certifications": [],
            "languages": [_element("German (B1)", [language_claim_id])],
            "positioning_guidance": {"resume_language": "en"},
        }
        with scripted_generation(response):
            draft = build_resume_draft(application)
        self.assertIn("Did the work.", draft.rendered_markdown)
        self.assertIn("German (B1)", draft.rendered_markdown)


def _make_jra_for(application) -> JobRequirementAnalysis:
    return JobRequirementAnalysis.objects.create(
        job_application=application, version=1,
        source_type=JobRequirementAnalysis.SourceType.PASTED,
        original_input="A pasted job posting about a role.",
        extracted_text="A pasted job posting about a role.",
        extracted_text_sha256="0" * 64, posting_language="en",
        employer="Globex Corporation", role_title="Senior Backend Engineer",
    )
