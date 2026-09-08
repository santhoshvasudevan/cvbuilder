import json
import subprocess
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

REPO_ROOT = Path(settings.BASE_DIR)

# V1 identifiers/patterns that must never appear in V2 source (docs/V2_REUSE_AUDIT.md;
# CLAUDE.md; docs/ARCHITECTURE.md Section 17).
FORBIDDEN_SUBSTRINGS = ("AC_NORMALIZE", "AC_RANK", "AC_MATCH")

SOURCE_DIRS = ("config", "job_applications", "templates")


class NoLegacyV1SemanticsTests(SimpleTestCase):
    def test_no_forbidden_v1_stage_substrings_in_source(self):
        offending = []
        for dirname in SOURCE_DIRS:
            for path in (REPO_ROOT / dirname).rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix not in {".py", ".html", ".txt", ".toml", ".yml", ".yaml"}:
                    continue
                if "migrations" in path.parts and path.name != "__init__.py":
                    continue  # generated migration files are exempt from source-text scanning
                if "tests" in path.parts:
                    continue  # this test suite legitimately names the forbidden strings as literals
                text = path.read_text(errors="ignore")
                for needle in FORBIDDEN_SUBSTRINGS:
                    if needle in text:
                        offending.append(f"{path}: contains {needle!r}")
        self.assertEqual(offending, [], "\n".join(offending))


class SecretHandlingInvariantTests(SimpleTestCase):
    def test_env_file_is_not_tracked_by_git(self):
        result = subprocess.run(
            ["git", "ls-files", ".env"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "", ".env must never be committed")

    def test_env_example_contains_no_real_looking_secret_values(self):
        env_example = REPO_ROOT / ".env.example"
        self.assertTrue(env_example.exists())
        text = env_example.read_text()
        # Every credential line must be blank or an obvious placeholder -- never a value that
        # looks like a real key.
        for line in text.splitlines():
            if "API_KEY=" in line or "PASSWORD=" in line or "SECRET_KEY=" in line:
                _, _, value = line.partition("=")
                value = value.strip()
                if not value:
                    continue
                self.assertTrue(
                    "change-me" in value or "change me" in value.lower(),
                    f"suspicious non-placeholder value in .env.example: {line!r}",
                )


class DetectSecretsStillDetectsRealSecretsTests(SimpleTestCase):
    """Proves the two narrowly-scoped `# pragma: allowlist secret` annotations in
    config/settings.py and job_applications/tests/test_admin.py (both audited false
    positives -- a labeled dev-only fallback key and a test-only password) did not broadly
    weaken detect-secrets. A realistic, unannotated high-entropy secret in a fresh file must
    still be flagged by the exact scan command `make secrets` runs.
    """

    def test_unallowlisted_high_entropy_secret_is_still_flagged(self):
        # Written to the OS temp directory, never the repo tree, so this test cannot leave a
        # stray file behind in the working tree even if it is interrupted mid-run.
        # Not a real credential: a synthetic, realistic-shaped secret used only to prove
        # detect-secrets still fires on unannotated matches when written to a throwaway file.
        # The pragma below allowlists this *source* line only (same mechanism as the other two
        # audited false positives) -- it is a Python comment, not part of the string value, so
        # the temp fixture file written from `secret_line` below carries no pragma and the
        # regression test still exercises a genuinely unannotated match.
        secret_line = (
            'AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"'  # pragma: allowlist secret
        )
        fixture_content = (
            "# Not a real credential -- synthetic fixture for a detect-secrets regression test.\n"
            f"{secret_line}\n"
        )
        # The same console-script entry point `make secrets` uses (python -m detect_secrets.main
        # produces no output under this package's CLI wiring), resolved relative to the active
        # interpreter so it works regardless of where the virtualenv is rooted.
        detect_secrets_bin = Path(sys.executable).with_name("detect-secrets")

        with tempfile.TemporaryDirectory() as tmp_dir:
            fixture_path = Path(tmp_dir) / "fixture_with_a_real_looking_secret.py"
            fixture_path.write_text(fixture_content)

            result = subprocess.run(
                [str(detect_secrets_bin), "scan", fixture_path.name],
                cwd=tmp_dir,
                capture_output=True,
                text=True,
                check=True,
            )
            report = json.loads(result.stdout)
            self.assertIn(
                fixture_path.name,
                report["results"],
                "detect-secrets no longer flags an unannotated high-entropy secret -- "
                "detection has been weakened",
            )


class MigrationReproducibilityTests(SimpleTestCase):
    def test_job_applications_has_an_initial_migration(self):
        migrations_dir = REPO_ROOT / "job_applications" / "migrations"
        migration_files = sorted(
            p.name for p in migrations_dir.glob("0*.py")
        )
        self.assertTrue(
            any(name.startswith("0001_") for name in migration_files),
            "job_applications must have a committed 0001 initial migration",
        )
