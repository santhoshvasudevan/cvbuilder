"""Fresh PostgreSQL migration apply / no-op / unapply-reapply checks for candidate_memory."""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class CandidateMemoryMigrationReproducibilityTests(SimpleTestCase):
    def test_initial_migration_file_exists(self):
        migrations = Path(settings.BASE_DIR) / "candidate_memory" / "migrations"
        files = sorted(path.name for path in migrations.glob("0*.py"))
        self.assertTrue(any(name.startswith("0001_") for name in files))

    def test_fresh_disposable_postgres_migrate_noop_and_reapply(self):
        """Spin a disposable Postgres, migrate from zero, second migrate no-op, unapply/reapply."""
        if os.environ.get("CVBUILDER_SKIP_DOCKER_MIGRATION_TEST") == "1":
            self.skipTest("Docker migration test skipped by environment flag")

        repo = Path(settings.BASE_DIR)
        container = f"cvbuilder-m3a-mig-{uuid.uuid4().hex[:8]}"
        host_port = "55433"
        password = settings.DATABASES["default"]["PASSWORD"]
        db_name = "cvbuilder"
        user = "cvbuilder"

        def run(args, env=None, check=True):
            return subprocess.run(
                args,
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=check,
            )

        # Ensure docker is available.
        docker_ok = subprocess.run(["docker", "info"], capture_output=True, text=True)
        if docker_ok.returncode != 0:
            self.skipTest("Docker is not available for disposable migration verification")

        try:
            run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--rm",
                    "--name",
                    container,
                    "-e",
                    f"POSTGRES_DB={db_name}",
                    "-e",
                    f"POSTGRES_USER={user}",
                    "-e",
                    f"POSTGRES_PASSWORD={password}",
                    "-p",
                    f"{host_port}:5432",
                    "postgres:16-alpine",
                ]
            )
            ready = False
            for _ in range(30):
                probe = subprocess.run(
                    ["docker", "exec", container, "pg_isready", "-U", user, "-d", db_name],
                    capture_output=True,
                    text=True,
                )
                if probe.returncode == 0:
                    ready = True
                    break
                time.sleep(1)
            self.assertTrue(ready, "disposable Postgres did not become ready")

            env = os.environ.copy()
            env.update(
                {
                    "POSTGRES_DB": db_name,
                    "POSTGRES_USER": user,
                    "POSTGRES_PASSWORD": password,
                    "POSTGRES_HOST": "127.0.0.1",
                    "POSTGRES_PORT": host_port,
                    "DJANGO_SETTINGS_MODULE": "config.settings",
                }
            )
            python = str(repo / ".venv" / "bin" / "python")

            first = run([python, "manage.py", "migrate", "--noinput"], env=env)
            self.assertIn("Applying candidate_memory.0001_", first.stdout + first.stderr)
            self.assertIn("Applying candidate_memory.0002_", first.stdout + first.stderr)
            self.assertIn("Applying candidate_memory.0003_", first.stdout + first.stderr)

            second = run([python, "manage.py", "migrate", "--noinput"], env=env)
            combined = second.stdout + second.stderr
            self.assertIn("No migrations to apply", combined)

            # Unapply only candidate_memory, then reapply.
            unapply = run(
                [python, "manage.py", "migrate", "candidate_memory", "zero", "--noinput"],
                env=env,
            )
            self.assertIn("candidate_memory", unapply.stdout + unapply.stderr)

            reapply = run([python, "manage.py", "migrate", "candidate_memory", "--noinput"], env=env)
            self.assertIn("Applying candidate_memory.0001_", reapply.stdout + reapply.stderr)
            self.assertIn("Applying candidate_memory.0002_", reapply.stdout + reapply.stderr)
            self.assertIn("Applying candidate_memory.0003_", reapply.stdout + reapply.stderr)

            drift = run(
                [python, "manage.py", "makemigrations", "--check", "--dry-run"],
                env=env,
            )
            self.assertEqual(drift.returncode, 0)
        finally:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, text=True)
