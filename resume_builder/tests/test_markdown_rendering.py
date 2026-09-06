from __future__ import annotations

from django.test import TestCase

from candidate_matching.services.retrieve import RetrievalContext, RetrievedClaim, RetrievedEngagement
from candidate_memory.models import CareerEngagement

from ..models import ResumeElement
from ..rendering.markdown import render_resume_markdown
from ..validators.no_fabrication import ValidatedElement
from .factories import make_engagement


def _retrieval(claims_by_engagement: dict[str, str | None]) -> RetrievalContext:
    claims = [
        RetrievedClaim(
            claim_id=cid, text="t", claim_type="responsibility", subject_scope="s",
            approved_engagement_ids=(eid,) if eid else (),
        )
        for cid, eid in claims_by_engagement.items()
    ]
    # D-035: the renderer now sources which engagement headers to render from `retrieval.
    # engagements` (the baseline chronology), never from which engagement a bullet happened to
    # cite -- so this fixture builder derives it from the same engagement IDs the claims above
    # reference, mirroring what `services/context.py` would have populated for a real build.
    engagement_ids = sorted({eid for eid in claims_by_engagement.values() if eid})
    engagements = [
        RetrievedEngagement(
            engagement_id=engagement.engagement_id,
            approved_role_title=engagement.approved_role_title,
            displayed_organization=engagement.displayed_organization,
            location=engagement.location,
            is_current=engagement.is_current,
            duration_months=engagement.duration_months(),
        )
        for engagement in CareerEngagement.objects.filter(engagement_id__in=engagement_ids)
    ]
    return RetrievalContext(candidate_memory_id=1, claims=claims, engagements=engagements, rules=[])


class RenderResumeMarkdownTests(TestCase):
    def test_renders_expected_top_level_structure(self):
        engagement = make_engagement()
        elements = [
            ValidatedElement(
                section=ResumeElement.Section.SUMMARY,
                engagement_id="",
                order=1,
                text="A strong backend engineer.",
                supporting_memory_claim_ids=["MC-1"],
                matched_job_requirement_ids=[],
            ),
            ValidatedElement(
                section=ResumeElement.Section.EXPERIENCE_BULLET,
                engagement_id=engagement.engagement_id,
                order=1,
                text="Owned the payments service.",
                supporting_memory_claim_ids=["MC-2"],
                matched_job_requirement_ids=[],
            ),
            ValidatedElement(
                section=ResumeElement.Section.SKILL,
                engagement_id="",
                order=1,
                text="Python",
                supporting_memory_claim_ids=["MC-3"],
                matched_job_requirement_ids=[],
            ),
        ]
        markdown = render_resume_markdown(
            elements,
            recommended_title="Senior Backend Engineer",
            retrieval=_retrieval({"MC-2": engagement.engagement_id}),
        )
        self.assertTrue(markdown.startswith("# Senior Backend Engineer\n"))
        self.assertIn("## Professional Summary", markdown)
        self.assertIn("- A strong backend engineer.", markdown)
        self.assertIn("## Professional Experience", markdown)
        self.assertIn(engagement.approved_role_title, markdown)
        self.assertIn(engagement.displayed_organization, markdown)
        self.assertIn("- Owned the payments service.", markdown)
        self.assertIn("## Key Skills", markdown)
        self.assertIn("Python", markdown)

    def test_omits_certifications_and_languages_when_absent(self):
        markdown = render_resume_markdown(
            [
                ValidatedElement(
                    section=ResumeElement.Section.SUMMARY,
                    engagement_id="",
                    order=1,
                    text="Summary.",
                    supporting_memory_claim_ids=["MC-1"],
                    matched_job_requirement_ids=[],
                )
            ],
            recommended_title="Engineer",
            retrieval=_retrieval({}),
        )
        self.assertNotIn("## Certifications", markdown)
        self.assertNotIn("## Languages", markdown)

    def test_includes_certifications_and_languages_when_present(self):
        markdown = render_resume_markdown(
            [
                ValidatedElement(
                    section=ResumeElement.Section.CERTIFICATION,
                    engagement_id="",
                    order=1,
                    text="AWS Certified",
                    supporting_memory_claim_ids=["MC-1"],
                    matched_job_requirement_ids=[],
                ),
                ValidatedElement(
                    section=ResumeElement.Section.LANGUAGE,
                    engagement_id="",
                    order=1,
                    text="German (B1)",
                    supporting_memory_claim_ids=["MC-2"],
                    matched_job_requirement_ids=[],
                ),
            ],
            recommended_title="Engineer",
            retrieval=_retrieval({}),
        )
        self.assertIn("## Certifications", markdown)
        self.assertIn("- AWS Certified", markdown)
        self.assertIn("## Languages", markdown)
        self.assertIn("- German (B1)", markdown)

    def test_experience_sections_render_most_recent_first(self):
        older = make_engagement(
            start_year=2010,
            start_month=1,
            end_status=CareerEngagement.EndStatus.KNOWN,
            end_year=2015,
            end_month=1,
        )
        newer = make_engagement(
            start_year=2016,
            start_month=1,
            end_status=CareerEngagement.EndStatus.PRESENT,
        )
        elements = [
            ValidatedElement(
                section=ResumeElement.Section.EXPERIENCE_BULLET,
                engagement_id=older.engagement_id,
                order=1,
                text="Older role bullet.",
                supporting_memory_claim_ids=["MC-1"],
                matched_job_requirement_ids=[],
            ),
            ValidatedElement(
                section=ResumeElement.Section.EXPERIENCE_BULLET,
                engagement_id=newer.engagement_id,
                order=1,
                text="Newer role bullet.",
                supporting_memory_claim_ids=["MC-2"],
                matched_job_requirement_ids=[],
            ),
        ]
        markdown = render_resume_markdown(
            elements,
            recommended_title="Engineer",
            retrieval=_retrieval({"MC-1": older.engagement_id, "MC-2": newer.engagement_id}),
        )
        self.assertLess(markdown.index("Newer role bullet."), markdown.index("Older role bullet."))

    def test_achievement_covered_by_an_existing_bullet_is_not_duplicated(self):
        elements = [
            ValidatedElement(
                section=ResumeElement.Section.SUMMARY,
                engagement_id="",
                order=1,
                text="Summary text.",
                supporting_memory_claim_ids=["MC-1"],
                matched_job_requirement_ids=[],
            ),
            ValidatedElement(
                section=ResumeElement.Section.ACHIEVEMENT,
                engagement_id="",
                order=1,
                text="Achievement text.",
                supporting_memory_claim_ids=["MC-1"],
                matched_job_requirement_ids=[],
            ),
        ]
        markdown = render_resume_markdown(elements, recommended_title="Engineer", retrieval=_retrieval({}))
        self.assertNotIn("Achievement text.", markdown)

    def test_uncovered_achievement_mapped_to_one_engagement_is_placed_there(self):
        engagement = make_engagement()
        elements = [
            ValidatedElement(
                section=ResumeElement.Section.EXPERIENCE_BULLET,
                engagement_id=engagement.engagement_id,
                order=1,
                text="Bullet.",
                supporting_memory_claim_ids=["MC-1"],
                matched_job_requirement_ids=[],
            ),
            ValidatedElement(
                section=ResumeElement.Section.ACHIEVEMENT,
                engagement_id="",
                order=1,
                text="Won an award.",
                supporting_memory_claim_ids=["MC-2"],
                matched_job_requirement_ids=[],
            ),
        ]
        markdown = render_resume_markdown(
            elements,
            recommended_title="Engineer",
            retrieval=_retrieval({"MC-1": engagement.engagement_id, "MC-2": engagement.engagement_id}),
        )
        experience_section = markdown.split("## Professional Experience")[1].split("## Key Skills")[0]
        self.assertIn("Won an award.", experience_section)

    def test_uncovered_achievement_with_ambiguous_engagement_falls_back_to_summary(self):
        engagement_a = make_engagement()
        engagement_b = make_engagement()
        elements = [
            ValidatedElement(
                section=ResumeElement.Section.EXPERIENCE_BULLET,
                engagement_id=engagement_a.engagement_id,
                order=1,
                text="Bullet A.",
                supporting_memory_claim_ids=["MC-A"],
                matched_job_requirement_ids=[],
            ),
            ValidatedElement(
                section=ResumeElement.Section.EXPERIENCE_BULLET,
                engagement_id=engagement_b.engagement_id,
                order=1,
                text="Bullet B.",
                supporting_memory_claim_ids=["MC-B"],
                matched_job_requirement_ids=[],
            ),
            ValidatedElement(
                section=ResumeElement.Section.ACHIEVEMENT,
                engagement_id="",
                order=1,
                text="Cross-role achievement.",
                supporting_memory_claim_ids=["MC-C", "MC-D"],
                matched_job_requirement_ids=[],
            ),
        ]
        markdown = render_resume_markdown(
            elements,
            recommended_title="Engineer",
            retrieval=_retrieval(
                {
                    "MC-A": engagement_a.engagement_id,
                    "MC-B": engagement_b.engagement_id,
                    "MC-C": engagement_a.engagement_id,
                    "MC-D": engagement_b.engagement_id,
                }
            ),
        )
        summary_section = markdown.split("## Professional Summary")[1].split("## Professional Experience")[0]
        self.assertIn("Cross-role achievement.", summary_section)

    def test_never_renders_planning_only_content(self):
        markdown = render_resume_markdown(
            [
                ValidatedElement(
                    section=ResumeElement.Section.SUMMARY,
                    engagement_id="",
                    order=1,
                    text="Summary.",
                    supporting_memory_claim_ids=["MC-1"],
                    matched_job_requirement_ids=[],
                )
            ],
            recommended_title="Engineer",
            retrieval=_retrieval({}),
        )
        for forbidden in ("title_options", "PositioningTheme", "positioning_guidance"):
            self.assertNotIn(forbidden, markdown)
