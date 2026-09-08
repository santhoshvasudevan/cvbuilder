import importlib
import os
import sys
from unittest import mock

from django.conf import settings
from django.db import connection
from django.test import Client, SimpleTestCase, TestCase, override_settings

# V1 app names that must not appear until their own milestone (M2 llm_provider, M3A
# candidate_memory, M3B candidate_context, M4 job_intake, M5 candidate_matching/
# positioning_strategy, M6 resume_builder, and reviews for Gate 1/2) creates them.
NOT_YET_BUILT_APPS = {
    "llm_provider",
    "candidate_memory",
    "candidate_context",
    "job_intake",
    "candidate_matching",
    "positioning_strategy",
    "resume_builder",
    "reviews",
}


class DatabaseConfigurationTests(TestCase):
    def test_engine_is_postgresql(self):
        self.assertEqual(settings.DATABASES["default"]["ENGINE"], "django.db.backends.postgresql")

    def test_no_sqlite_anywhere_in_database_config(self):
        for alias, config in settings.DATABASES.items():
            self.assertNotIn("sqlite", config["ENGINE"].lower(), f"sqlite found in DATABASES[{alias!r}]")

    def test_live_connection_vendor_is_postgresql(self):
        self.assertEqual(connection.vendor, "postgresql")

    def test_live_connection_can_execute_a_query(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            self.assertEqual(cursor.fetchone(), (1,))


class SettingsValidationTests(TestCase):
    def test_secret_key_is_set_and_non_empty(self):
        self.assertTrue(settings.SECRET_KEY)

    def test_allowed_hosts_is_a_non_empty_list(self):
        self.assertIsInstance(settings.ALLOWED_HOSTS, list)
        self.assertGreater(len(settings.ALLOWED_HOSTS), 0)

    def test_timezone_is_utc_and_use_tz_enabled(self):
        self.assertEqual(settings.TIME_ZONE, "UTC")
        self.assertTrue(settings.USE_TZ)

    def test_static_and_template_configuration_present(self):
        self.assertTrue(settings.STATIC_URL)
        self.assertIn(settings.BASE_DIR / "templates", settings.TEMPLATES[0]["DIRS"])

    def test_logging_is_configured(self):
        self.assertIn("handlers", settings.LOGGING)
        self.assertIn("formatters", settings.LOGGING)
        self.assertIn("console", settings.LOGGING["handlers"])

    def test_only_m1_pipeline_app_is_installed(self):
        # docs/IMPLEMENTATION_PLAN.md M1 scope / V2-D036: only job_applications is implemented
        # in M1 -- every other app boundary is created when its own milestone begins.
        self.assertIn("job_applications", settings.INSTALLED_APPS)
        installed = set(settings.INSTALLED_APPS)
        overlap = installed & NOT_YET_BUILT_APPS
        self.assertEqual(overlap, set(), f"apps installed ahead of their milestone: {overlap}")

    def test_default_auto_field_is_configured(self):
        self.assertEqual(settings.DEFAULT_AUTO_FIELD, "django.db.models.BigAutoField")


class SafeErrorDefaultsTests(TestCase):
    @override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
    def test_404_does_not_leak_debug_information(self):
        client = Client(raise_request_exception=False)
        response = client.get("/this-path-does-not-exist/")
        self.assertEqual(response.status_code, 404)
        body = response.content.decode(errors="ignore")
        for marker in ("Traceback", "Request Method:", "Django Version", settings.SECRET_KEY):
            self.assertNotIn(marker, body)

    def test_debug_true_locally_does_not_leak_secret_key_via_home_page(self):
        # Sanity check regardless of DEBUG: the home page must never echo SECRET_KEY.
        response = self.client.get("/")
        body = response.content.decode(errors="ignore")
        self.assertNotIn(settings.SECRET_KEY, body)


class ProductionSecretKeyEnforcementTests(SimpleTestCase):
    """config/settings.py must fail closed: DEBUG=False with no DJANGO_SECRET_KEY configured
    anywhere must raise RuntimeError rather than silently falling back to the dev-only insecure
    key. `dotenv.load_dotenv` is patched to a no-op so a real local `.env` file -- present in
    every dev checkout, never committed, and normally supplying a real key -- cannot mask the
    condition under test. The `config.settings` module is reloaded to re-run its module-level
    SECRET_KEY logic, and unconditionally reloaded again with the real environment restored so
    this test cannot leave the shared module in a broken state for tests that run after it.
    """

    def test_debug_false_without_secret_key_raises_runtime_error(self):
        settings_module = sys.modules["config.settings"]
        env_backup = os.environ.copy()
        try:
            os.environ["DJANGO_DEBUG"] = "false"
            os.environ.pop("DJANGO_SECRET_KEY", None)
            with mock.patch("dotenv.load_dotenv", return_value=False):
                with self.assertRaises(RuntimeError):
                    importlib.reload(settings_module)
        finally:
            os.environ.clear()
            os.environ.update(env_backup)
            importlib.reload(settings_module)
        self.assertTrue(
            settings_module.SECRET_KEY, "settings module must be fully restored after reload"
        )
