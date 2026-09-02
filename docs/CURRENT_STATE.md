# Current State

Last updated: 2026-09-02 (M1 — Django/PostgreSQL application foundation implemented and verified).

## Summary

This repository has completed **Milestone M1**: a running Django project against a local
Dockerized PostgreSQL, with the seven pipeline app boundaries from `docs/ARCHITECTURE.md` §2
scaffolded. No business logic, no models beyond Django's own built-in apps, and no LLM provider
calls exist yet — M1 is scaffolding only, exactly as scoped in `docs/IMPLEMENTATION_PLAN.md`.
Milestones M2 (LLM provider abstraction) through M8 remain **not implemented**.

## What exists

### Planning documents (M0/M0.1)
- `requirements.md` — the authoritative product requirements (v1.1.1 / "M0.1").
- `docs/ARCHITECTURE.md`, `docs/IMPLEMENTATION_PLAN.md`, `docs/DECISIONS.md` (D-001..D-015),
  `docs/TEST_STRATEGY.md`, `docs/REQUIREMENT_TRACEABILITY.md`, `docs/RESUME_OUTPUT_STRUCTURE.md`,
  `docs/CANDIDATE_MEMORY_SNAPSHOT.md`, `CLAUDE.md` — see prior entries in this file's history
  (git log) for what each covers; unchanged in substance by M1.
- `docs/AC/AC-MEMORY_PROFILE.md`, `docs/AC/AC-profile_english.md`, `docs/AC/AC-profile_german.md`
  — committed, operator-approved Candidate Memory bootstrap evidence (still unused until M3).

### Application foundation (M1 — new)
- **Django project** (`config/`): `manage.py`, `config/settings.py`, `config/urls.py`,
  `config/wsgi.py`, `config/asgi.py`. Django 5.1 on Python 3.11.
- **Seven scaffolded apps**, one per `docs/ARCHITECTURE.md` §2 boundary, each with only the
  default `apps.py`/`admin.py`/`models.py`/`views.py`/`tests.py`/`migrations/` Django generates —
  **no model fields, no views, no business logic in any of them**: `llm_provider`,
  `candidate_memory`, `job_intake`, `candidate_matching`, `resume_builder`, `reviews`,
  `job_applications`. All seven are registered in `INSTALLED_APPS`.
  - **Deliberate scope decision**: `job_applications` does **not** get the `JobApplication` model's
    fields at M1, even though `docs/IMPLEMENTATION_PLAN.md` M1 named this as a possibility. Its
    `current_jra`/`current_fit_assessment`/`current_resume_draft` foreign keys point at models
    (`JobRequirementAnalysis`, `FitAssessment`, `ResumeDraft`) that don't exist until M4/M5/M6 —
    defining them now would mean forward-referencing apps that aren't built yet. M1 keeps
    `job_applications` scaffolding-only like every other app; the `JobApplication` model is built
    in Milestone M4 instead, per `docs/IMPLEMENTATION_PLAN.md` M4's own "populated with real fields
    if not already done at M1" phrasing. This is a documented interpretation of an ambiguous plan
    sentence, not a scope violation — recorded here per `CLAUDE.md`'s instruction to report
    deviations honestly.
- **Settings** (`config/settings.py`): `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, and all four
  `DATABASES` values are read from environment variables (via `python-dotenv` loading `.env`),
  never hardcoded. `DEBUG` defaults on for local dev; a missing `DJANGO_SECRET_KEY` raises at
  import time once `DJANGO_DEBUG` is false, rather than silently falling back to an insecure key
  in a non-debug context. `DATABASES` targets PostgreSQL only — no SQLite fallback.
- **`docker-compose.yml`**: a single `db` service (`postgres:16-alpine`), reading
  `POSTGRES_DB`/`USER`/`PASSWORD`/`PORT` from the environment with local-dev defaults, a named
  volume, and a healthcheck. No other services (no Redis, no broker — per STACK-003/NG-004).
- **`.env.example`**: placeholders only (no real values) for Django config, Postgres config, and
  the three provider credential variable names (`OPENAI_API_KEY`, `NVIDIA_NIM_API_KEY`,
  `GEMINI_API_KEY`) that the M2 provider registry will reference by name. A real `.env` exists
  locally for development and is gitignored — never committed.
- **`.gitignore`**: covers `.env`, `.venv/`, Python bytecode/egg-info, Django `staticfiles/`/
  `media/`/`*.sqlite3`, and `.DS_Store` (new occurrences only — the pre-existing tracked
  `docs/.DS_Store` was deliberately left untouched, per explicit prior instruction not to handle
  it as part of documentation work; it remains a known, harmless pre-existing artifact).
- **`templates/base.html`**: a minimal server-rendered base template (STACK-004 — no SPA), wired
  into `TEMPLATES[0]["DIRS"]`.
- **`requirements.txt`** (Django, psycopg[binary], python-dotenv, pydantic) and
  **`requirements-dev.txt`** (adds ruff). **`pyproject.toml`** holds ruff's configuration.
- **`Makefile`**: `make check` (`manage.py check`), `make test` (`manage.py test`), `make lint`
  (`ruff check .`), plus `migrate`/`makemigrations`/`run`/`superuser`/`up`/`down` — the repeatable
  local quality commands M1 requires; no remote CI was added or is required to consider M1 done.
- **`.venv/`** — local virtual environment (gitignored, not committed) with all of the above
  installed.

## What does not exist

- Any model fields, migrations beyond Django's own built-in apps, views, or templates for any of
  the seven pipeline apps.
- Any LLM provider adapters, registry data, or `LLMCallLog` rows (Milestone M2).
- Any Candidate Memory bootstrap command, source ingestion, or claims (Milestone M3).
- Any tests beyond Django's default empty `tests.py` stubs (0 tests currently defined).
- Any remote/CI configuration (not required for M1; may be added later without blocking anything).

## M1 verification performed (2026-09-02)

- `docker compose up -d` → `db` container reached `healthy` status (Postgres 16, local volume).
- `python manage.py migrate` → applied all built-in Django migrations (contenttypes, auth, admin,
  sessions) against the real Postgres instance cleanly, zero errors.
- `python manage.py check` → "System check identified no issues (0 silenced)."
- `make check`, `make lint` (ruff, after auto-fixing Django's own boilerplate unused-import
  scaffolding across all seven apps), and `make test` (0 tests, exits 0) all pass repeatably.
- Django admin verified reachable and login-capable end-to-end: created a throwaway local
  superuser (`createsuperuser --noinput`, random password, local dev DB only, never committed),
  started the dev server, confirmed `GET /admin/login/` → 200, `POST` with valid credentials → 302,
  and the post-redirect `/admin/` page rendered "Site administration"/"Log out" — i.e. a real
  logged-in session, not just a reachable page. Dev server was stopped afterward; verification
  artifacts (cookies, HTML, password note) were not retained.
- `git status`/manual review confirmed no secret value is staged or committed: `.env` is
  gitignored and was never staged; a grep of all newly-created tracked-candidate files for
  credential-shaped strings found nothing.

## Decisions (see `docs/DECISIONS.md` for full detail)

D-001 through D-015 are all APPROVED (several "with modification"); D-013 is superseded by D-010.
No decision remains blocking for any milestone through M7 — D-004's fetch-library choice is
deferred to M4 by design, not blocked; the D-001 orchestration-framework re-evaluation is
deliberately deferred to a post-M7 checkpoint.

## Next action

Milestone M2 (LLM provider abstraction, registry, and audit foundation) is next, per
`docs/IMPLEMENTATION_PLAN.md`. M3 and M4 both depend only on M2, not on each other, and may proceed
in either order after M2 completes.

## Maintenance rule for this file

Update this file after any milestone completes or any meaningful implementation step lands.
Describe the repository as it truly is — never mark something present, tested, or working that
has not actually been built and verified.
