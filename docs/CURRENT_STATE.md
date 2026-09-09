# Current State

**Do not treat this file as self-certifying.** It records the last agent's understanding at the time it was written. Verify it against the repository — `git log`, `git status`, and actual tests — before relying on it. See `docs/HANDOVER_PROTOCOL.md`.

## Repository State

- V2 development branch: `cvbuild2`, currently at the M1 re-audit correction commit (`a7921b9`, "test: close M1 re-audit findings") — the M2 correction below has not been merged into it yet. M2 was implemented on `m2-llm-provider-foundation` (branched from that `cvbuild2` HEAD, M2 implementation commit `a9a9494`); this correction is a direct child of `a9a9494` on the same branch, addressing an independent M2 re-audit's findings — always re-verify with `git log -1 --oneline` and `git branch --show-current` rather than trusting any commit hash in this document, since this document's own commit will move the current branch's HEAD forward once committed.
- `main` is the **V1 legacy line** (currently `d5bdcea`), confirmed by an independent M1 re-audit to have diverged from `cvbuild2` at `fd02af8` with its own unrelated, incompatible M1/M2/later-milestone history. `main` is a read-only reuse source only (V2-D017) — it is never a merge target, and `cvbuild2` is never merged into it. Promoting `cvbuild2` to the repository's default branch is a distinct, future, separately-authorized decision (V2-D038).
- Last verified date: 2026-09-09

## Current Milestone

- Milestone: M2 — LLM Control Plane and Operator Call Console.
- Status: independent re-audit (range `a7921b9..a9a9494`) returned **M2 RE-AUDIT FAILED — CORRECTION REQUIRED** (one BLOCKER, one MAJOR, one MINOR finding, one documentation-accuracy observation — see `docs/DECISIONS.md`'s "M2 Independent Re-Audit Correction" section, V2-D042/V2-D043). This commit is the correction, on `m2-llm-provider-foundation`, a direct child of `a9a9494`. It has **not** been independently re-audited or merged into `cvbuild2` yet — that is the next step, not something this document can self-certify. M1 (Foundation and Reuse Audit) and its independent re-audit correction remain complete on `cvbuild2`. M3A has not started.

## Verified Working

- **M1 (Django/PostgreSQL foundation):** `config/`, `job_applications` app, PostgreSQL-only configuration, admin, structured logging, minimal home view/template — all run for real. `JobApplication`/`StageRun`/`JobApplicationStageState` migrated against real PostgreSQL. Canonical stage vocabulary (`StageIdentifier`) matches `docs/ARCHITECTURE.md` §5. See `docs/DECISIONS.md` V2-D036/V2-D037/V2-D038 for the full M1 history and its independent re-audit correction.
- **M2 (LLM provider foundation):**
  - `llm_provider` app: `LLMProvider`/`LLMModel`/`StageModelAssignment`/`LLMCallLog` match `docs/ARCHITECTURE.md` §12/§13 (documented field-set deviations, V2-D039, V2-D042). `supported_reasoning_levels` is the sole source of reasoning capability (V2-D034); `LLMModel.supports_reasoning` is a derived property, never independently stored.
  - Four real provider adapters (OpenAI, NVIDIA NIM, Gemini, OpenRouter) plus a deterministic `FakeAdapter`, all sharing one call path (`BaseLLMAdapter.generate()`: pre-flight validation → retry → Pydantic re-validation → `LLMCallLog` write). No provider SDK is imported anywhere (confirmed by source grep) — only `requests`.
  - Pre-flight "fails before HTTP" validation (`llm_provider.validation`) checks inactive provider/model, missing/unset credential, unsupported structured output, unsupported reasoning level, output-budget-exceeds-capability, and (as of this correction, V2-D042) temperature capability/range/reasoning-combination — all before any adapter's `_call_once` (the actual HTTP boundary) runs. No automatic provider/model fallback exists anywhere in this app (verified: retry re-calls only the same closure; `compare_models` never substitutes one model's result for another's).
  - `job_applications.StageRun`'s deferred fields (`provider`, `model`, `reasoning_level`, `max_output_tokens`, `temperature` — V2-D036) are implemented, migrated against real PostgreSQL, and null for deterministic stages.
  - `llm_provider.services.console` (`run_stage_manually`, `run_with_model_override`, `compare_models`) plus Django admin are M2's UI surface (V2-D040); interactive per-call run/approve/edit/rerun UI is deferred to M4, the first milestone with a real `StageRun`-producing stage to attach it to.
  - `manage.py llm_smoke_test <provider>` is the opt-in manual smoke-test command — never run automatically, reports `NOT_LIVE_VERIFIED` without any network call when no credential is configured (the case in this environment; no live provider has ever been exercised here).
  - **This correction (V2-D042/V2-D043):** the Gemini adapter now sends its credential via the documented `x-goog-api-key` header, never the URL (closes the audit BLOCKER); `sanitize_error_message` independently redacts sensitive URL/query-string parameters, URL user-info, and auth-scheme tokens as a defence-in-depth invariant; adapters classify network-boundary exceptions by type (`NormalizedLLMError.from_network_exception`) rather than passing raw exception text through; `temperature` is now validated pre-flight against explicit model capability metadata and a canonical numeric range (closes the audit MAJOR); a regression test pins `job_applications._REASONING_LEVEL_VALUES` against `llm_provider.models.ReasoningLevel.values` (closes the audit MINOR).
  - 220/220 automated tests pass (`make test`; 52 `job_applications` + 168 `llm_provider`, up from 51/120 pre-correction — 49 new tests, all closing audit findings). `manage.py check` clean. `ruff check .` clean. `makemigrations --check --dry-run` reports no drift. `detect-secrets scan` reports zero findings, reproducibly. Every real-adapter test mocks `requests.post` — zero live network calls anywhere in the suite.
  - Fresh-migration verification used disposable, isolated PostgreSQL containers (never the operational `cvbuilder-db-1` container/`cvbuilder_postgres_data` volume): forward migration from empty applies cleanly (including the new `llm_provider/migrations/0002_llmmodel_supports_temperature_and_more.py`), a second run is a no-op, and migrations unapply/reapply cleanly.
- Every documented `Makefile` target remains individually confirmed working (see M1 history in `docs/DECISIONS.md`); `make verify`/`make secrets`/`make test`/`make check`/`make lint`/`make migrations-check` were all re-run against the corrected tree specifically.

## In Progress

- This correction awaits a fresh independent re-audit (range `a9a9494..<this commit>`) and, if it passes, merge into `cvbuild2` (per this repository's branch/merge convention — see `docs/HANDOVER_PROTOCOL.md`).

## Known Issues / Risks

- Real adapters (OpenAI/NVIDIA NIM/Gemini/OpenRouter) have never been exercised against a live provider in this environment — no credentials are configured. Schema translation and request construction are verified with `requests.post` mocked; this mirrors `main`'s own original M2 commit's documented limitation at the same point.
- `requirements.txt` includes `pydantic` and `requests` (added for M2). `readability-lxml` remains intentionally absent — still unneeded until M4's URL-fetch work.
- Two non-blocking implementation choices remain open from M0.2, to be settled during their own milestones: whether `ExperienceSlot` metadata is copied at creation or resolved by reference to its source engagement record (M3A); exact stored enum naming for the AJ requirement taxonomy (M4).
- Interactive per-call run/approve/edit/rerun UI (`requirements.md` UI-001..006) is not yet built — deliberately deferred to M4 (V2-D040), not an oversight.
- Per-model temperature ceilings narrower than the canonical `[0.0, 2.0]` range are not represented (V2-D042 only models whether temperature is supported at all, and whether it may combine with reasoning) — not needed by any model in the current registry; revisit if a future model requires a narrower ceiling.

## Verification

Commands run against the corrected M2 tree (`m2-llm-provider-foundation`, this commit), in order, with results:

```
manage.py check                     -> System check identified no issues (0 silenced).
make migrations-check               -> No changes detected.
make test                           -> Ran 220 tests in ~1.9s. OK. (52 job_applications + 168 llm_provider)
make lint                           -> All checks passed!
make secrets                        -> detect-secrets scan: "results": {} (zero findings).
make verify                         -> All verification checks passed.
git diff --check                    -> clean, no output.
```

Fresh-migration verification (isolated, disposable PostgreSQL containers, never the operational volume):

```
docker run ... postgres:16-alpine (disposable, port 5545) -> forward migrate from empty: all
  migrations including llm_provider.0002_llmmodel_supports_temperature_and_more applied
  cleanly; second migrate: "No migrations to apply."; migrate llm_provider 0001_initial then
  migrate: unapply/reapply of 0002 clean; makemigrations --check --dry-run: no drift.
```

The operational database (`cvbuilder-db-1` container / `cvbuilder_postgres_data` volume) was never started, connected to, or modified by this correction work.

For the full M1 implementation and its independent re-audit correction (three non-blocking findings, all closed), see `docs/DECISIONS.md` V2-D036 through V2-D038 — not restated here. For the M2 implementation decisions and this correction's findings/fixes, see V2-D039 through V2-D043.

## Last Completed Handover

- Outgoing: Claude Code, this session — M2 independent-re-audit correction (BLOCKER/MAJOR/MINOR closure), on branch `m2-llm-provider-foundation`, a direct child of `a9a9494`.
- This is informational only — do not make correctness dependent on which tool wrote this. Verify the repository directly per `docs/HANDOVER_PROTOCOL.md` §B regardless of who the outgoing agent was.

## Next Recommended Action

1. Independently re-audit this correction (range `a9a9494..<this commit>`) before merging `m2-llm-provider-foundation` into `cvbuild2`.
2. If the re-audit passes, fast-forward merge into `cvbuild2` and update this document's Repository State accordingly.
3. Begin M3A (Candidate Knowledge and StaticResumeProfile) per `docs/IMPLEMENTATION_PLAN.md` from the resulting `cvbuild2` HEAD.
4. Use `docs/MILESTONE_COMPLETION_CHECKLIST.md` before declaring M3A complete.

## Working Tree Expectations

Clean except for local, intentionally-untracked files that must never be committed: `.env`, `.venv/`, `.claude/`, `.DS_Store`, `.ruff_cache/`. If `git status --short` shows anything else, investigate before proceeding — see `docs/HANDOVER_PROTOCOL.md` §B.

## Full Decision and Reuse History

This file intentionally does not restate project history. For the full list of closed architectural decisions, see `docs/DECISIONS.md` (as of this update: V2-D001 through V2-D043). For the full `main`-branch reuse classification, see `docs/V2_REUSE_AUDIT.md`.
