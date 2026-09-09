# Current State

**Do not treat this file as self-certifying.** It records the last agent's understanding at the time it was written. Verify it against the repository — `git log`, `git status`, and actual tests — before relying on it. See `docs/HANDOVER_PROTOCOL.md`.

## Repository State

- V2 development branch: `cvbuild2`, currently at the M1 re-audit correction commit (`a7921b9`, "test: close M1 re-audit findings") — none of the M2 work below has been merged into it yet. M2 line, in order: `a9a9494` (original M2 implementation) → `5e6a40c` (first correction: Gemini credential-leak BLOCKER, temperature-validation MAJOR, reasoning-vocabulary MINOR) → this commit (second correction: temperature-capability-default MAJOR and two MINORs found by a second independent re-audit of `5e6a40c`). All four commits live on local branch `worktree-m2-correction`; `m2-llm-provider-foundation` still points at `a9a9494` only and has intentionally not been moved. Always re-verify with `git log -1 --oneline` and `git branch --show-current` rather than trusting any commit hash in this document.
- `main` is the **V1 legacy line** (currently `d5bdcea`), confirmed by an independent M1 re-audit to have diverged from `cvbuild2` at `fd02af8` with its own unrelated, incompatible M1/M2/later-milestone history. `main` is a read-only reuse source only (V2-D017) — it is never a merge target, and `cvbuild2` is never merged into it. Promoting `cvbuild2` to the repository's default branch is a distinct, future, separately-authorized decision (V2-D038).
- Last verified date: 2026-09-09

## Current Milestone

- Milestone: M2 — LLM Control Plane and Operator Call Console.
- Status: a second independent re-audit of the M2 line (range `a7921b9..5e6a40c`) confirmed the first correction's BLOCKER/MAJOR/MINOR genuinely closed, but found one new MAJOR (temperature capability flags defaulted fail-open) and two new MINORs in that correction itself. This commit is the Product-Owner-directed fix (V2-D044) — see `docs/DECISIONS.md`'s "M2 Second Independent Re-Audit Correction" section. It has **not** yet been independently re-audited or merged into `cvbuild2` — that is the next step, not something this document can self-certify. M1 (Foundation and Reuse Audit) remains complete on `cvbuild2`. M3A has not started.

## Verified Working

- **M1 (Django/PostgreSQL foundation):** `config/`, `job_applications` app, PostgreSQL-only configuration, admin, structured logging, minimal home view/template — all run for real. `JobApplication`/`StageRun`/`JobApplicationStageState` migrated against real PostgreSQL. Canonical stage vocabulary (`StageIdentifier`) matches `docs/ARCHITECTURE.md` §5. See `docs/DECISIONS.md` V2-D036/V2-D037/V2-D038 for the full M1 history and its independent re-audit correction.
- **M2 (LLM provider foundation):**
  - `llm_provider` app: `LLMProvider`/`LLMModel`/`StageModelAssignment`/`LLMCallLog` match `docs/ARCHITECTURE.md` §12/§13 (documented field-set deviations, V2-D039, V2-D042, V2-D044). `supported_reasoning_levels` is the sole source of reasoning capability (V2-D034); `LLMModel.supports_reasoning` is a derived property, never independently stored.
  - Four real provider adapters (OpenAI, NVIDIA NIM, Gemini, OpenRouter) plus a deterministic `FakeAdapter`, all sharing one call path (`BaseLLMAdapter.generate()`: pre-flight validation → retry → Pydantic re-validation → `LLMCallLog` write). No provider SDK is imported anywhere (confirmed by source grep) — only `requests`.
  - Pre-flight "fails before HTTP" validation (`llm_provider.validation`) checks inactive provider/model, missing/unset credential, unsupported structured output, unsupported reasoning level, output-budget-exceeds-capability, and temperature capability/range/reasoning-combination — all before any adapter's `_call_once` (the actual HTTP boundary) runs. No automatic provider/model fallback exists anywhere in this app (verified: retry re-calls only the same closure; `compare_models` never substitutes one model's result for another's).
  - `job_applications.StageRun`'s deferred fields (`provider`, `model`, `reasoning_level`, `max_output_tokens`, `temperature` — V2-D036) are implemented, migrated against real PostgreSQL, and null for deterministic stages.
  - `llm_provider.services.console` (`run_stage_manually`, `run_with_model_override`, `compare_models`) plus Django admin are M2's UI surface (V2-D040); interactive per-call run/approve/edit/rerun UI is deferred to M4.
  - `manage.py llm_smoke_test <provider>` is the opt-in manual smoke-test command — never run automatically.
  - **First correction (V2-D042/V2-D043, commit `5e6a40c`):** Gemini adapter credential moved to the documented `x-goog-api-key` header; `sanitize_error_message`/`classify_network_exception` hardened as defence-in-depth; temperature validated pre-flight; reasoning-vocabulary drift guard added.
  - **This correction (V2-D044):** `LLMModel.supports_temperature`/`supports_temperature_with_reasoning` now default `False` (fail-closed) instead of `True` — a model may use `temperature` only after explicit per-model opt-in, matching the existing `supports_structured_output`/`supported_reasoning_levels` pattern. `llm_provider/migrations/0002_llmmodel_supports_temperature_and_more.py` was edited in place (not amended-around) to carry the corrected default, since it was confirmed unpublished (never merged into `cvbuild2`, never pushed, only ever applied to disposable test databases) before editing. `LLMModel.clean()` now rejects `supports_temperature_with_reasoning=True` combined with `supports_temperature=False`. A new adapter-level test proves an invalid temperature configuration never reaches `requests.post`; a new parity test proves save-time (`StageModelAssignment.clean()`) and call-time (`validate_temperature_supported`) validation agree on every tested configuration.
  - 231/231 automated tests pass (`make test`; 52 `job_applications` + 179 `llm_provider`, up from 220 pre-this-correction — 11 new tests, all closing the second re-audit's findings). `manage.py check` clean. `ruff check .` clean. `makemigrations --check --dry-run` reports no drift. `detect-secrets scan` reports zero findings, reproducibly. Every real-adapter test mocks `requests.post` — zero live network calls anywhere in the suite.
  - Fresh-migration verification used disposable, isolated PostgreSQL containers (never the operational `cvbuilder-db-1` container/`cvbuilder_postgres_data` volume): forward migration from empty applies cleanly, a second run is a no-op, migrations unapply/reapply cleanly, and — specifically tested per this correction — a representative pre-existing `LLMModel` row (reasoning-capable, created before `0002` applies) receives `supports_temperature=False`/`supports_temperature_with_reasoning=False` on migration, confirming fail-closed backfill behavior empirically, not just by inspection.
- Every documented `Makefile` target remains individually confirmed working; `make verify`/`make secrets`/`make test`/`make check`/`make lint`/`make migrations-check` were all re-run against this corrected tree specifically.

## In Progress

- This correction awaits a fresh independent re-audit and, if it passes, merge into `cvbuild2` (per this repository's branch/merge convention — see `docs/HANDOVER_PROTOCOL.md`).

## Known Issues / Risks

- Real adapters (OpenAI/NVIDIA NIM/Gemini/OpenRouter) have never been exercised against a live provider in this environment — no credentials are configured.
- `requirements.txt` includes `pydantic` and `requests` (added for M2). `readability-lxml` remains intentionally absent — still unneeded until M4's URL-fetch work.
- Two non-blocking implementation choices remain open from M0.2: whether `ExperienceSlot` metadata is copied at creation or resolved by reference (M3A); exact stored enum naming for the AJ requirement taxonomy (M4).
- Interactive per-call run/approve/edit/rerun UI (`requirements.md` UI-001..006) is not yet built — deliberately deferred to M4 (V2-D040), not an oversight.
- Per-model temperature ceilings narrower than the canonical `[0.0, 2.0]` range are not represented — not needed by any model in the current (empty) registry; revisit if a future model requires a narrower ceiling.
- The registry currently has zero seeded/fixture `LLMModel` rows in any environment — the fail-closed defaults (V2-D044) mean every real model added from M4 onward must have its temperature capability explicitly configured before temperature-based calls will validate.

## Verification

Commands run against this corrected M2 tree, in order, with results:

```
manage.py check                     -> System check identified no issues (0 silenced).
make migrations-check               -> No changes detected.
make test                           -> Ran 231 tests in ~2.0s. OK. (52 job_applications + 179 llm_provider)
make lint                           -> All checks passed!
make secrets                        -> detect-secrets scan: "results": {} (zero findings).
make verify                         -> All verification checks passed.
git diff --check                    -> clean, no output.
```

Fresh-migration verification (isolated, disposable PostgreSQL container, never the operational volume):

```
docker run ... postgres:16-alpine (disposable) -> forward migrate from empty: all migrations
  including the corrected llm_provider.0002_llmmodel_supports_temperature_and_more (default=False
  for both new fields) applied cleanly; second migrate: "No migrations to apply."; unapply
  llm_provider to 0001, insert a representative pre-existing LLMModel row via raw SQL, reapply ->
  row receives supports_temperature=False/supports_temperature_with_reasoning=False (fail-closed
  backfill, confirmed empirically); makemigrations --check --dry-run: no drift.
```

The operational database (`cvbuilder-db-1` container / `cvbuilder_postgres_data` volume) was never started, connected to, or modified by this correction work.

For the full M1 implementation and its independent re-audit correction, see `docs/DECISIONS.md` V2-D036 through V2-D038. For the M2 implementation decisions and both corrections' findings/fixes, see V2-D039 through V2-D044.

## Last Completed Handover

- Outgoing: Claude Code, this session — second M2 correction (temperature-capability fail-closed defaults, V2-D044), on branch `worktree-m2-correction`, a direct child of `5e6a40c`.
- This is informational only — do not make correctness dependent on which tool wrote this. Verify the repository directly per `docs/HANDOVER_PROTOCOL.md` §B regardless of who the outgoing agent was.

## Next Recommended Action

1. Independently re-audit this correction before merging `worktree-m2-correction` into `cvbuild2`.
2. If the re-audit passes, fast-forward merge into `cvbuild2` and update this document's Repository State accordingly.
3. Begin M3A (Candidate Knowledge and StaticResumeProfile) per `docs/IMPLEMENTATION_PLAN.md` from the resulting `cvbuild2` HEAD.
4. Use `docs/MILESTONE_COMPLETION_CHECKLIST.md` before declaring M3A complete.

## Working Tree Expectations

Clean except for local, intentionally-untracked files that must never be committed: `.env`, `.venv/`, `.claude/`, `.DS_Store`, `.ruff_cache/`. If `git status --short` shows anything else, investigate before proceeding — see `docs/HANDOVER_PROTOCOL.md` §B.

## Full Decision and Reuse History

This file intentionally does not restate project history. For the full list of closed architectural decisions, see `docs/DECISIONS.md` (as of this update: V2-D001 through V2-D044). For the full `main`-branch reuse classification, see `docs/V2_REUSE_AUDIT.md`.
