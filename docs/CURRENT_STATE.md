# Current State

**Do not treat this file as self-certifying.** It records the last agent's understanding at the time it was written. Verify it against the repository — `git log`, `git status`, and actual tests — before relying on it. See `docs/HANDOVER_PROTOCOL.md`.

## Repository State

- V2 development branch: `cvbuild2`, currently at the M1 re-audit correction commit (`a7921b9`, "test: close M1 re-audit findings"). This M2 work was implemented on a dedicated branch, `m2-llm-provider-foundation`, branched from that `cvbuild2` HEAD — always re-verify with `git log -1 --oneline` and `git branch --show-current` rather than trusting this value; this document's own commit will move the current branch's HEAD forward once committed.
- `main` is the **V1 legacy line** (currently `d5bdcea`), confirmed by an independent M1 re-audit to have diverged from `cvbuild2` at `fd02af8` with its own unrelated, incompatible M1/M2/later-milestone history. `main` is a read-only reuse source only (V2-D017) — it is never a merge target, and `cvbuild2` is never merged into it. Promoting `cvbuild2` to the repository's default branch is a distinct, future, separately-authorized decision (V2-D038).
- Last verified date: 2026-09-08

## Current Milestone

- Milestone: M2 — LLM Control Plane and Operator Call Console.
- Status: **COMPLETE** on `m2-llm-provider-foundation` (pending independent re-audit and merge into `cvbuild2`). M1 (Foundation and Reuse Audit) and its independent re-audit correction are complete on `cvbuild2`. M3A has not started.

## Verified Working

- **M1 (Django/PostgreSQL foundation):** `config/`, `job_applications` app, PostgreSQL-only configuration, admin, structured logging, minimal home view/template — all run for real. `JobApplication`/`StageRun`/`JobApplicationStageState` migrated against real PostgreSQL. Canonical stage vocabulary (`StageIdentifier`) matches `docs/ARCHITECTURE.md` §5. See `docs/DECISIONS.md` V2-D036/V2-D037/V2-D038 for the full M1 history and its independent re-audit correction.
- **M2 (LLM provider foundation), this update:**
  - `llm_provider` app: `LLMProvider`/`LLMModel`/`StageModelAssignment`/`LLMCallLog` match `docs/ARCHITECTURE.md` §12/§13 (two documented field-set deviations, V2-D039). `supported_reasoning_levels` is the sole source of reasoning capability (V2-D034); `LLMModel.supports_reasoning` is a derived property, never independently stored.
  - Four real provider adapters (OpenAI, NVIDIA NIM, Gemini, OpenRouter) plus a deterministic `FakeAdapter`, all sharing one call path (`BaseLLMAdapter.generate()`: pre-flight validation → retry → Pydantic re-validation → `LLMCallLog` write). No provider SDK is imported anywhere (confirmed by source grep) — only `requests`.
  - Pre-flight "fails before HTTP" validation (`llm_provider.validation`) checks inactive provider/model, missing/unset credential, unsupported structured output, unsupported reasoning level, and output-budget-exceeds-capability — all before any adapter's `_call_once` (the actual HTTP boundary) runs. No automatic provider/model fallback exists anywhere in this app (verified: retry re-calls only the same closure; `compare_models` never substitutes one model's result for another's).
  - `job_applications.StageRun`'s deferred fields (`provider`, `model`, `reasoning_level`, `max_output_tokens`, `temperature` — V2-D036) are now implemented, migrated against real PostgreSQL, and null for deterministic stages.
  - `llm_provider.services.console` (`run_stage_manually`, `run_with_model_override`, `compare_models`) plus Django admin are M2's UI surface (V2-D040); interactive per-call run/approve/edit/rerun UI is deferred to M4, the first milestone with a real `StageRun`-producing stage to attach it to.
  - `manage.py llm_smoke_test <provider>` is the opt-in manual smoke-test command — never run automatically, reports `NOT_LIVE_VERIFIED` without any network call when no credential is configured (the case in this environment; no live provider has ever been exercised here).
  - 171/171 automated tests pass (`make test`; 51 `job_applications` + 120 `llm_provider`). `manage.py check` clean. `ruff check .` clean. `makemigrations --check --dry-run` reports no drift. `detect-secrets scan` reports zero findings, reproducibly. Every real-adapter test mocks `requests.post` — zero live network calls anywhere in the suite.
  - Fresh-migration verification used two disposable, isolated PostgreSQL containers (never the operational `cvbuilder_postgres_data` volume): forward migration from empty applies cleanly, a second run is a no-op, and both `llm_provider`/`job_applications` migrations unapply/reapply cleanly.
- Every documented `Makefile` target remains individually confirmed working (see M1 history in `docs/DECISIONS.md`); `make verify`/`make secrets`/`make test`/`make check`/`make lint`/`make migrations-check` were all re-run against the M2 tree specifically.

## In Progress

- M2 awaits independent re-audit and, if it passes, merge into `cvbuild2` (per this repository's branch/merge convention — see `docs/HANDOVER_PROTOCOL.md`).

## Known Issues / Risks

- Real adapters (OpenAI/NVIDIA NIM/Gemini/OpenRouter) have never been exercised against a live provider in this environment — no credentials are configured. Schema translation and request construction are verified with `requests.post` mocked; this mirrors `main`'s own original M2 commit's documented limitation at the same point.
- `requirements.txt` now includes `pydantic` and `requests` (added for M2, as anticipated in the prior version of this document). `readability-lxml` remains intentionally absent — still unneeded until M4's URL-fetch work.
- Two non-blocking implementation choices remain open from M0.2, to be settled during their own milestones: whether `ExperienceSlot` metadata is copied at creation or resolved by reference to its source engagement record (M3A); exact stored enum naming for the AJ requirement taxonomy (M4).
- Interactive per-call run/approve/edit/rerun UI (`requirements.md` UI-001..006) is not yet built — deliberately deferred to M4 (V2-D040), not an oversight.

## Verification

Commands run against the M2 tree (`m2-llm-provider-foundation`), in order, with results:

```
manage.py check                     -> System check identified no issues (0 silenced).
make migrations-check               -> No changes detected.
make test                           -> Ran 171 tests in ~1.8s. OK. (51 job_applications + 120 llm_provider)
make lint                           -> All checks passed!
make secrets                        -> detect-secrets scan: "results": {} (zero findings).
make verify                         -> All verification checks passed.
git diff --check                    -> clean, no output.
```

Fresh-migration verification (isolated, disposable PostgreSQL containers, never the operational volume):

```
docker run ... postgres:16-alpine (port 5435, db/user m2_audit)  -> forward migrate: all
  migrations including llm_provider.0001_initial and job_applications.0002/0003 applied
  cleanly; second migrate: "No migrations to apply."; migrate llm_provider zero / migrate
  job_applications zero then migrate: unapply/reapply both clean.
```

Operational database (`cvbuilder-db-1` / volume `cvbuilder_postgres_data`) was migrated normally (not reset) and its row counts (all M1 + M2 tables: 0 rows) were verified unchanged before/after this work.

For the full M1 implementation and its independent re-audit correction (three non-blocking findings, all closed), see `docs/DECISIONS.md` V2-D036 through V2-D038 — not restated here.

## Last Completed Handover

- Outgoing: Claude Code, this session — M2 (LLM Control Plane and Operator Call Console) implementation, on branch `m2-llm-provider-foundation`.
- This is informational only — do not make correctness dependent on which tool wrote this. Verify the repository directly per `docs/HANDOVER_PROTOCOL.md` §B regardless of who the outgoing agent was.

## Next Recommended Action

1. Independently re-audit M2 (this document's evidence, the actual diff, and reproduced test/verification commands) before merging `m2-llm-provider-foundation` into `cvbuild2`.
2. If the re-audit passes, merge (fast-forward if possible) into `cvbuild2` and update this document's Repository State accordingly.
3. Begin M3A (Candidate Knowledge and StaticResumeProfile) per `docs/IMPLEMENTATION_PLAN.md` from the resulting `cvbuild2` HEAD.
4. Use `docs/MILESTONE_COMPLETION_CHECKLIST.md` before declaring M3A complete.

## Working Tree Expectations

Clean except for local, intentionally-untracked files that must never be committed: `.env`, `.venv/`, `.claude/`, `.DS_Store`, `.ruff_cache/`. If `git status --short` shows anything else, investigate before proceeding — see `docs/HANDOVER_PROTOCOL.md` §B.

## Full Decision and Reuse History

This file intentionally does not restate project history. For the full list of closed architectural decisions, see `docs/DECISIONS.md` (as of this update: V2-D001 through V2-D041). For the full `main`-branch reuse classification, see `docs/V2_REUSE_AUDIT.md`.
