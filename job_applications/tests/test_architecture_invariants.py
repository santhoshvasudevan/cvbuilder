import subprocess
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
