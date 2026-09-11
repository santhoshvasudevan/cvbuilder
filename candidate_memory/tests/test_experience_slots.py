from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from candidate_memory.integrity import (
    HardIntegrityError,
    assert_hard_integrity,
    reject_model_static_metadata_write,
    validate_static_resume_profile,
)
from candidate_memory.models import (
    CandidateMemory,
    CandidateProfile,
    CareerEngagement,
    ExperienceSlot,
    OperatorCorrection,
    PositioningHistoryEntry,
    StaticResumeProfile,
)
from candidate_memory.services.experience_slots import (
    ExperienceSlotServiceError,
    create_or_activate_slot,
    ensure_static_profile,
    set_slot_order,
)


def _engagement(memory, company, sort_hint=0):
    return CareerEngagement.objects.create(
        memory=memory,
        company_name=company,
        role_title=f"{company} Role",
        location="Test City",
        start_date="01/2020",
        end_date_or_present="Present",
        sort_hint=sort_hint,
    )


class ExperienceSlotServiceTests(TestCase):
    def setUp(self):
        self.memory = CandidateMemory.objects.create(label="slots", is_active=True)
        self.profile = ensure_static_profile(self.memory)
        self.e1 = _engagement(self.memory, "Alpha", 1)
        self.e2 = _engagement(self.memory, "Beta", 2)
        self.e3 = _engagement(self.memory, "Gamma", 3)
        self.e4 = _engagement(self.memory, "Delta", 4)

    def test_copy_metadata_and_retain_source_linkage(self):
        slot = create_or_activate_slot(
            profile=self.profile,
            engagement=self.e1,
            sequence=1,
        )
        self.assertEqual(slot.company_name, self.e1.company_name)
        self.assertEqual(slot.role_title, self.e1.role_title)
        self.assertEqual(slot.career_engagement_id, self.e1.pk)
        self.assertFalse(hasattr(StaticResumeProfile, "slot_1_company"))

    def test_explicit_ordering_and_hard_integrity_pass(self):
        set_slot_order(self.profile, [self.e1.pk, self.e2.pk, self.e3.pk])
        assert_hard_integrity(self.profile)
        sequences = list(
            ExperienceSlot.objects.filter(
                static_resume_profile=self.profile, is_active=True, is_primary=True
            )
            .order_by("sequence")
            .values_list("sequence", flat=True)
        )
        self.assertEqual(sequences, [1, 2, 3])

    def test_cardinality_failure_for_two_slots(self):
        create_or_activate_slot(profile=self.profile, engagement=self.e1, sequence=1)
        create_or_activate_slot(profile=self.profile, engagement=self.e2, sequence=2)
        findings = validate_static_resume_profile(self.profile)
        codes = {finding.code for finding in findings}
        self.assertIn("SLOT_CARDINALITY", codes)
        with self.assertRaises(HardIntegrityError):
            assert_hard_integrity(self.profile)

    def test_duplicate_engagement_rejected_by_form_path_service(self):
        with self.assertRaises(ExperienceSlotServiceError):
            set_slot_order(self.profile, [self.e1.pk, self.e1.pk, self.e2.pk])

    def test_unique_active_primary_sequence_constraint(self):
        create_or_activate_slot(profile=self.profile, engagement=self.e1, sequence=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExperienceSlot.objects.create(
                    static_resume_profile=self.profile,
                    career_engagement=self.e2,
                    sequence=1,
                    is_primary=True,
                    is_active=True,
                    company_name=self.e2.company_name,
                    role_title=self.e2.role_title,
                    location=self.e2.location,
                    start_date=self.e2.start_date,
                    end_date_or_present=self.e2.end_date_or_present,
                )

    def test_model_cannot_overwrite_static_metadata(self):
        slot = create_or_activate_slot(profile=self.profile, engagement=self.e1, sequence=1)
        findings = reject_model_static_metadata_write(
            slot,
            {"company_name": "Model Invented Corp", "role_title": slot.role_title},
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "STATIC_METADATA_REPLACEMENT")
        slot.refresh_from_db()
        self.assertEqual(slot.company_name, "Alpha")

    def test_no_automatic_selection_helper_exists(self):
        import candidate_memory.services.experience_slots as slot_services

        self.assertFalse(hasattr(slot_services, "auto_select_primary_slots"))
        self.assertFalse(hasattr(slot_services, "choose_top_engagements"))


class ExperienceSlotUITests(TestCase):
    def setUp(self):
        self.client = Client()
        self.memory = CandidateMemory.objects.create(label="ui", is_active=True)
        self.profile = ensure_static_profile(self.memory)
        self.engagements = [
            _engagement(self.memory, "One", 1),
            _engagement(self.memory, "Two", 2),
            _engagement(self.memory, "Three", 3),
        ]

    def test_get_workspace(self):
        url = reverse("candidate_memory:experience_slots", args=[self.memory.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Operator ExperienceSlot selection")
        self.assertContains(response, "HARD_INTEGRITY")

    def test_post_explicit_selection_orders_and_validates(self):
        url = reverse("candidate_memory:experience_slots", args=[self.memory.pk])
        response = self.client.post(
            url,
            {
                "slot_1": self.engagements[0].pk,
                "slot_2": self.engagements[1].pk,
                "slot_3": self.engagements[2].pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        assert_hard_integrity(self.profile)
        self.assertEqual(
            ExperienceSlot.objects.filter(
                static_resume_profile=self.profile, is_active=True, is_primary=True
            ).count(),
            3,
        )

    def test_post_duplicate_selection_rejected(self):
        url = reverse("candidate_memory:experience_slots", args=[self.memory.pk])
        response = self.client.post(
            url,
            {
                "slot_1": self.engagements[0].pk,
                "slot_2": self.engagements[0].pk,
                "slot_3": self.engagements[1].pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "distinct CareerEngagement")
        self.assertEqual(ExperienceSlot.objects.filter(static_resume_profile=self.profile).count(), 0)


class ProfileDurabilityTests(TestCase):
    def test_profile_preferences_and_history_persist(self):
        memory = CandidateMemory.objects.create(label="profile", is_active=True)
        profile = CandidateProfile.objects.create(
            memory=memory,
            career_direction="Cloud product architecture",
            target_role_families=["Solutions Architect", "Technical Product Owner"],
            known_gaps=["formal people management"],
            development_areas=["Go"],
            technology_depth={"AWS": "transferable from GCP", "Spark": "exposure only"},
            privacy_preferences="Prefer limited personal contact details",
            employer_naming_preferences="Prefer JV/alliance framing for partner names",
            preferred_positioning="Hands-on cloud/data product ownership",
        )
        StaticResumeProfile.objects.create(
            memory=memory,
            candidate_name="Test Candidate",
            static_certifications=["Google Cloud Associate Cloud Engineer"],
            static_languages=[{"language": "English", "level": "fluent"}],
        )
        OperatorCorrection.objects.create(
            memory=memory,
            field_path="technology_depth.AWS",
            correction_text="Do not claim deep AWS production ownership",
        )
        PositioningHistoryEntry.objects.create(
            memory=memory,
            employer_or_context="Example Corp application",
            summary="Lead with connected-vehicle cloud ownership",
            was_successful=True,
        )
        profile.refresh_from_db()
        self.assertEqual(profile.target_role_families[0], "Solutions Architect")
        self.assertEqual(
            memory.static_resume_profile.static_certifications[0],
            "Google Cloud Associate Cloud Engineer",
        )
        self.assertEqual(memory.operator_corrections.count(), 1)
        self.assertEqual(memory.positioning_history.count(), 1)
