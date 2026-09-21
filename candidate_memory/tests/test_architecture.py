"""Architecture/import invariants for M3A — later apps must not be introduced yet."""

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

FORBIDDEN_APPS = (
    "candidate_context",
    "candidate_matching",
    "positioning_strategy",
    "resume_builder",
    "reviews",
    "job_intake",
)

FORBIDDEN_IMPORT_NEEDLES = tuple(
    f"import {name}" for name in FORBIDDEN_APPS
) + tuple(f"from {name}" for name in FORBIDDEN_APPS)


class M3AArchitectureBoundaryTests(SimpleTestCase):
    def test_later_apps_are_not_installed(self):
        installed = set(settings.INSTALLED_APPS)
        for app_name in FORBIDDEN_APPS:
            self.assertNotIn(app_name, installed)
        self.assertIn("candidate_memory", installed)

    def test_later_app_packages_do_not_exist(self):
        root = Path(settings.BASE_DIR)
        for app_name in FORBIDDEN_APPS:
            self.assertFalse(
                (root / app_name).exists(),
                f"{app_name} must not be introduced in M3A",
            )

    def test_candidate_memory_does_not_import_later_apps(self):
        root = Path(settings.BASE_DIR) / "candidate_memory"
        offenders = []
        for path in root.rglob("*.py"):
            if "migrations" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for needle in FORBIDDEN_IMPORT_NEEDLES:
                if needle in text:
                    offenders.append(f"{path}: {needle}")
        self.assertEqual(offenders, [])

    def test_no_provider_sdk_imports_in_candidate_memory(self):
        root = Path(settings.BASE_DIR) / "candidate_memory"
        forbidden = ("openai", "google.generativeai", "anthropic", "litellm")
        offenders = []
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for needle in forbidden:
                if f"import {needle}" in text or f"from {needle}" in text:
                    offenders.append(f"{path}: {needle}")
        self.assertEqual(offenders, [])

    def test_experience_slot_has_no_fixed_slot_columns_on_static_profile(self):
        from candidate_memory.models import ExperienceSlot, StaticResumeProfile

        static_field_names = {field.name for field in StaticResumeProfile._meta.fields}
        for forbidden in (
            "slot_1_company",
            "slot_2_company",
            "slot_3_company",
            "experience_slot_1",
            "experience_slot_2",
            "experience_slot_3",
        ):
            self.assertNotIn(forbidden, static_field_names)
        self.assertTrue(
            any(field.name == "static_resume_profile" for field in ExperienceSlot._meta.fields)
        )
        self.assertTrue(any(field.name == "sequence" for field in ExperienceSlot._meta.fields))
