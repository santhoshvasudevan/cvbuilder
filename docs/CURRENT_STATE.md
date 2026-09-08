# Current State

**Do not treat this file as self-certifying.** It records the last agent's understanding at the time it was written. Verify it against the repository — `git log`, `git status`, and actual tests — before relying on it. See `docs/HANDOVER_PROTOCOL.md`.

## Repository State

- Branch: `cvbuild2`
- Last verified HEAD (parent of this commit): `9d91d19` ("docs: establish multi-agent engineering and handover protocol") — this document's own commit will move HEAD forward once committed; always re-verify with `git log -1 --oneline` rather than trusting this value.
- Last verified date: 2026-09-08

## Current Milestone

- Milestone: M1 — Foundation and Reuse Audit.
- Status: **COMPLETE.** M2 (LLM Control Plane) has not started.

## Verified Working

- Django project foundation (`config/`), one Django app (`job_applications`), PostgreSQL-only configuration, admin, structured logging, and a minimal home view/template all run for real — not just import-checked.
- `JobApplication`, `StageRun`, `JobApplicationStageState` models exist, migrated (`job_applications/migrations/0001_initial.py`) against a real local PostgreSQL 16 instance, and match the closed M0.2 design (`docs/ARCHITECTURE.md` §5) except `StageRun`'s provider/model fields, deferred to M2 (V2-D036).
- Canonical stage vocabulary (`StageIdentifier`) implemented exactly as `docs/ARCHITECTURE.md` §5 defines it; a test asserts no V1 stage identifiers (`AC_NORMALIZE`/`AC_RANK`/`AC_MATCH`) exist anywhere in source.
- 42/42 automated tests pass (`make test`). `manage.py check` clean. `ruff check .` clean. `makemigrations --check --dry-run` reports no drift. `detect-secrets scan` reports zero findings. `.env` confirmed untracked by Git (tested, not just gitignored).
- Every documented `Makefile` target was individually run and confirmed working from the actual shell in this session: `install`, `check`, `test`, `lint`, `migrate`, `makemigrations`, `migrations-check`, `secrets`, `verify`, `up`, `down`, `db-wait`, `start` (including a live HTTP request against the running dev server — home `200`, admin `302` unauthenticated), `stop`, `status`. `superuser` and `run` are thin, direct wrappers around standard Django management commands and were not separately exercised beyond confirming they invoke real commands.
- Infra files (`docker-compose.yml`, `.env.example`, `.gitignore`, `Makefile`, `pyproject.toml`, `requirements.txt`, `templates/base.html`, `manage.py`, `config/asgi.py`/`wsgi.py`) were cherry-picked file-by-file from `main` per `docs/V2_REUSE_AUDIT.md`'s REUSE_AS_IS classification, not bulk-merged. `config/settings.py`/`urls.py` were adapted (REUSE_WITH_ADAPTATION) to M1's actual single-app scope.

## In Progress

- Nothing. M1 is complete; M2 has not been started.

## Known Issues / Risks

- **Local dev database reset during M1 (V2-D037):** the local Postgres Docker volume already contained a full stale V1 schema and a colliding `("job_applications", "0001_initial")` migration record from unrelated prior work against the same container name. It was reset (`docker compose down -v` + fresh `up`) before verification. This is expected, disposable local state, not source-controlled data — see V2-D037 for the full explanation. A different agent picking up this repository should not be surprised if the local Postgres volume looks "new"; that is intentional.
- `requirements.txt`/`requirements-dev.txt` intentionally omit `pydantic`, `requests`, and `readability-lxml` (present in `main`'s equivalent files) because M1's actual code does not import them. They will be added when the milestone that needs them (M4 for URL fetch/`readability-lxml`, M2/M4+ for `pydantic` schemas, `requests` per adapter) begins — adding them now would be an unused dependency, not a needed one.
- Two non-blocking implementation choices remain open from M0.2, to be settled during their own milestones: whether `ExperienceSlot` metadata is copied at creation or resolved by reference to its source engagement record (M3A); exact stored enum naming for the AJ requirement taxonomy (M4).

## Verification

Commands actually run this session, in order, with results:

```
make check                          -> System check identified no issues (0 silenced).
make migrations-check               -> No changes detected.
make migrate                        -> applied cleanly to a fresh local PostgreSQL 16 database.
make test                           -> Ran 42 tests in ~0.5s. OK.
make lint                           -> All checks passed!
make secrets                        -> detect-secrets scan: "results": {} (zero findings).
git diff --check --cached           -> clean, no output.
```

Plus a live-server smoke test (`make start`, `curl` against `/` and `/admin/`, `make stop`) — see "Verified Working" above for results.

## Last Completed Handover

- Outgoing: Claude Code, this session — M1 (Foundation and Reuse Audit) implementation.
- This is informational only — do not make correctness dependent on which tool wrote this. Verify the repository directly per `docs/HANDOVER_PROTOCOL.md` §B regardless of who the outgoing agent was.

## Next Recommended Action

1. Begin M2 (LLM Control Plane and Operator Call Console) per `docs/IMPLEMENTATION_PLAN.md` — build/reuse `llm_provider` (provider registry, model registry with `supported_reasoning_levels` as canonical per V2-D034, `StageModelAssignment`, `LLMCallLog` referencing `job_applications.StageRun`, adapters, retry/error handling).
2. Once `llm_provider.LLMProvider`/`LLMModel` exist, add the deferred `StageRun` fields (`provider`, `model`, `reasoning_level`, `max_output_tokens`, `temperature`) via a new migration (V2-D036).
3. Use `docs/MILESTONE_COMPLETION_CHECKLIST.md` before declaring M2 complete.

## Working Tree Expectations

Clean except for local, intentionally-untracked files that must never be committed: `.env`, `.venv/`, `.claude/`, `.DS_Store`, `.ruff_cache/`. If `git status --short` shows anything else, investigate before proceeding — see `docs/HANDOVER_PROTOCOL.md` §B.

## Full Decision and Reuse History

This file intentionally does not restate project history. For the full list of closed architectural decisions, see `docs/DECISIONS.md` (as of this update: V2-D001 through V2-D037). For the full `main`-branch reuse classification, see `docs/V2_REUSE_AUDIT.md`.
