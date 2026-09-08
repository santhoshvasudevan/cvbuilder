# CVBuilder V2 Milestone Completion Checklist

**Status:** Canonical, tool-neutral, reusable for every milestone (M1, M2, M3A, M3B, M4, M5, M6, M7, M8, and any future one)
**Date:** 2026-09-08

Work through this checklist before declaring any milestone complete. It complements — and does not replace — the milestone-specific "Acceptance" criteria already defined for each milestone in `docs/IMPLEMENTATION_PLAN.md`; run through both.

## Checklist

- [ ] **Milestone scope confirmed** — re-read this milestone's Objective/Scope/Out-of-scope in `docs/IMPLEMENTATION_PLAN.md` immediately before claiming completion; confirm you built what was actually in scope, not more and not less.
- [ ] **Requirements mapped** — every requirement ID this milestone claims to satisfy is checked against `requirements.md` and `docs/REQUIREMENT_TRACEABILITY.md`.
- [ ] **Architecture decisions respected** — the implementation matches `docs/ARCHITECTURE.md` and the current `docs/DECISIONS.md` entries relevant to this milestone; no silent deviation.
- [ ] **Implementation complete** — the milestone's scope items are actually built, not stubbed with a plan to finish later.
- [ ] **Migrations checked** — any schema change has a corresponding Django migration; migrations apply cleanly from a fresh database (see `docs/ENGINEERING_RULES.md` §D).
- [ ] **Focused tests pass** — tests for the specific code you changed pass.
- [ ] **Milestone acceptance tests pass** — the milestone's own Acceptance criteria in `docs/IMPLEMENTATION_PLAN.md` are met, verified by running the actual tests/walkthroughs described, not by inspection alone.
- [ ] **No unintended network calls** — automated/deterministic tests do not reach live providers; live provider calls remain explicit opt-in only (`docs/TEST_STRATEGY.md` §2).
- [ ] **No secrets/local files added** — `git status`/`git diff` reviewed for `.env`, `.venv/`, credentials, PID/log files, caches, OS metadata (`docs/ENGINEERING_RULES.md` §G).
- [ ] **`git diff` reviewed** — the actual diff was read before staging, not just the file list.
- [ ] **`docs/CURRENT_STATE.md` updated** — reflects this milestone's real, verified state per its concise operational structure.
- [ ] **`docs/REQUIREMENT_TRACEABILITY.md` updated** — verification status/notes updated for the requirement IDs this milestone touched.
- [ ] **`docs/DECISIONS.md` / `docs/ARCHITECTURE.md` updated if anything changed** — any deviation from the plan discovered during implementation is recorded as a decision, not left implicit in code.
- [ ] **Known issues explicitly recorded** — anything incomplete, fragile, or deferred is written down, not silently omitted.
- [ ] **Next milestone prerequisites recorded** — what the next milestone needs from this one (per the dependency graph in `docs/IMPLEMENTATION_PLAN.md`) is confirmed present.
- [ ] **Final `git status` understood** — you know exactly what is committed, what is uncommitted, and why, before finishing.
- [ ] **Commit created only if authorized** — do not commit unless explicitly asked, or the task instructions explicitly authorize it (see `docs/ENGINEERING_RULES.md` §C).

## Milestone Completion Report Template

Use this as the report you hand to the product owner or the next agent (see `docs/HANDOVER_PROTOCOL.md` §A for the full handover sequence this feeds into).

```text
Milestone: <e.g. M3A — Candidate Knowledge and StaticResumeProfile>
Branch: <branch name>
HEAD after this work: <commit hash, or "uncommitted">

Scope confirmed: <yes/no, and note any scope adjustment>

Requirements covered: <requirement IDs, e.g. STATIC-001, V2-D024, V2-D031>

Files/components changed:
  <list>

Migrations:
  <list, or "none">

Tests run:
  <exact commands>
Results:
  <pass/fail counts, not just "passed">

Milestone acceptance criteria:
  <criterion 1> — met / not met — <evidence>
  <criterion 2> — met / not met — <evidence>
  ...

Known issues:
  <list, or "none known">

Docs updated:
  docs/CURRENT_STATE.md: <yes/no>
  docs/REQUIREMENT_TRACEABILITY.md: <yes/no>
  docs/DECISIONS.md / docs/ARCHITECTURE.md: <yes/no, or "no changes needed">

Next milestone prerequisites: <what the next milestone needs, and whether it's present>

Working tree: <clean / describe intentional uncommitted state>

Commit: <created (hash) / not created (why)>
```

## Completed Milestones

### M1 — Foundation and Reuse Audit

- [x] Milestone scope confirmed — `job_applications` app only (`JobApplication`/`StageRun`/`JobApplicationStageState`), Django/PostgreSQL foundation, no domain logic from later milestones (V2-D036).
- [x] Requirements mapped — `docs/REQUIREMENT_TRACEABILITY.md` rows for V2-D021/D022/V2-D035, LLM-012 marked VERIFIED for M1.
- [x] Architecture decisions respected — `docs/ARCHITECTURE.md` §5 schema followed exactly except the deferred `StageRun` provider/model fields (V2-D036, recorded, tested).
- [x] Implementation complete — models, admin, one URL/view, templates, settings, all Makefile-documented commands.
- [x] Migrations checked — `0001_initial.py` applies cleanly to a fresh PostgreSQL database; `makemigrations --check --dry-run` reports no drift.
- [x] Focused tests pass — all 7 test modules in `job_applications/tests/` pass individually and together.
- [x] Milestone acceptance tests pass — see `docs/IMPLEMENTATION_PLAN.md` M1 Acceptance section, marked MET with evidence.
- [x] No unintended network calls — no provider/adapter code exists yet in M1; nothing in the test suite reaches the network.
- [x] No secrets/local files added — `detect-secrets scan` clean (two audited false positives narrowly allowlisted inline per V2-D038, not excluded by file/directory/rule; a regression test proves detection is not weakened); `.env` confirmed untracked by an automated test; `.gitignore` covers `.env`/`.venv/`/`.claude/`/`.DS_Store`/caches.
- [x] `git diff` reviewed — `git diff --check --cached` clean; staged file list matches the intended M1 file set exactly (verified by explicit enumeration before commit).
- [x] `docs/CURRENT_STATE.md` updated — reflects verified M1 completion with command-level evidence.
- [x] `docs/REQUIREMENT_TRACEABILITY.md` updated — M1-scoped rows marked VERIFIED.
- [x] `docs/DECISIONS.md` / `docs/ARCHITECTURE.md` updated — V2-D036 (M1 app scope + deferred `StageRun` fields) and V2-D037 (local DB reset) recorded; `docs/ARCHITECTURE.md` §5 annotated with a pointer to V2-D036.
- [x] Known issues explicitly recorded — see `docs/CURRENT_STATE.md` Known Issues/Risks (local DB reset, trimmed `requirements.txt`, two open M3A/M4 implementation choices carried forward from M0.2).
- [x] Next milestone prerequisites recorded — M2 needs `llm_provider.LLMProvider`/`LLMModel` before `StageRun`'s deferred fields can be added; recorded in `docs/CURRENT_STATE.md` Next Recommended Action.
- [x] Final `git status` understood — clean except expected untracked local files.
- [x] Commit created only if authorized — authorized explicitly by this milestone's task instructions.

```text
Milestone: M1 — Foundation and Reuse Audit
Branch: cvbuild2
HEAD after this work: see docs/CURRENT_STATE.md (verify with `git log -1`)

Scope confirmed: yes — job_applications app only, no later-milestone domain logic

Requirements covered: V2-D021, V2-D022, V2-D024 (ExperienceSlot cardinality not yet
  applicable in M1), V2-D035, V2-D036, V2-D037, LLM-012

Files/components changed:
  config/ (settings, urls, asgi, wsgi, __init__)
  job_applications/ (models, admin, views, urls, apps, migrations/0001_initial, tests/*)
  templates/ (base.html adapted from main, home.html new)
  docker-compose.yml, .env.example, .gitignore, Makefile, pyproject.toml,
  requirements.txt, requirements-dev.txt, manage.py

Migrations:
  job_applications/0001_initial.py (JobApplication, StageRun, JobApplicationStageState)

Tests run:
  make test  (.venv/bin/python manage.py test)
Results:
  Ran 42 tests in ~0.5s. OK. (0 failures, 0 errors)

Milestone acceptance criteria (docs/IMPLEMENTATION_PLAN.md M1):
  application runs — met — make start; curl / -> 200, curl /admin/ -> 302
  Postgres works — met — migrate applied to real local PostgreSQL 16, tables verified
  admin works — met — JobApplication/StageRun/JobApplicationStageState registered, reachable
  tests/check/lint pass — met — 42/42 tests, check clean, ruff clean
  reuse decisions documented — met — docs/V2_REUSE_AUDIT.md; infra files cherry-picked
  JobApplication/StageRun/JobApplicationStageState schema matches M0.2 design — met,
    with StageRun provider/model fields deferred to M2 (V2-D036, explicit and tested)
  no accidental V1 pipeline semantics — met — automated source scan test

Known issues:
  Local Postgres dev volume contained stale V1 schema; reset before verification (V2-D037).
  requirements.txt intentionally omits pydantic/requests/readability-lxml (unused in M1).

Docs updated:
  docs/CURRENT_STATE.md: yes
  docs/REQUIREMENT_TRACEABILITY.md: yes
  docs/DECISIONS.md / docs/ARCHITECTURE.md: yes (V2-D036, V2-D037; ARCHITECTURE.md §5 note)
  docs/IMPLEMENTATION_PLAN.md: yes (M1 marked COMPLETE with evidence)
  docs/TEST_STRATEGY.md: yes (Foundation section marked VERIFIED)

Next milestone prerequisites: M2 needs llm_provider.LLMProvider/LLMModel to exist before
  StageRun's deferred fields (provider/model/reasoning_level/max_output_tokens/temperature)
  can be added via migration.

Working tree: clean except expected untracked local files (.env, .venv/, .claude/, .DS_Store)

Commit: created — explicit file staging, see commit message "feat: implement M1 Django
  foundation (job_applications app, PostgreSQL, Makefile)" or equivalent
```

### M1 Independent Re-Audit — Corrections (V2-D038)

An independent re-audit (`9d91d19..ea4bd48`) returned **PASSED WITH NON-BLOCKING FINDINGS**. All three were closed in one corrective commit, a direct child of `ea4bd48`:

1. `make secrets` did not reproduce this document's "zero findings" claim above — two audited false positives (dev-only `SECRET_KEY` fallback, test-only password) are now narrowly allowlisted inline (`# pragma: allowlist secret`); a new regression test (`DetectSecretsStillDetectsRealSecretsTests`) proves an unannotated realistic secret is still detected by the exact `make secrets` command.
2. Added `ProductionSecretKeyEnforcementTests` covering `DEBUG=False` + no `DJANGO_SECRET_KEY` anywhere (including `.env`) → `RuntimeError`.
3. Added `DeletionBehaviorTests` (6 tests) covering `JobApplication` → `StageRun`/`JobApplicationStageState` cascade deletion and `StageRun` → `JobApplicationStageState.current_stage_run`/`approved_stage_run` `SET_NULL` behavior.

Result: 50/50 tests pass, `make verify` passes, `make secrets` is clean and reproducible, no migration was generated, no M2/provider code was introduced. The same re-audit found `cvbuild2` cannot be fast-forward-merged into `main` (`main` is a divergent V1 legacy line, not a V2 integration branch — see V2-D038 for the full finding); no merge was performed or is implied by this correction.
