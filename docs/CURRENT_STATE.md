# Current State

**Do not treat this file as self-certifying.** It records the last agent's understanding at the time it was written. Verify it against the repository — `git log`, `git status`, and actual tests — before relying on it. See `docs/HANDOVER_PROTOCOL.md`.

## Repository State

- Branch: `cvbuild2`
- Last verified HEAD: `1b7c901` ("docs: finalize CVBuilder V2 architecture") — this document's own commit will move HEAD forward once committed; always re-verify with `git log -1 --oneline` rather than trusting this value.
- Last verified date: 2026-09-08

## Current Milestone

- Milestone: none active. Architecture closure (M0 / M0.2) is complete; M1 (Foundation and Reuse Audit implementation) has not started.
- Status: ready to begin M1.

## Verified Working

- The nine canonical planning documents (`requirements.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/IMPLEMENTATION_PLAN.md`, `docs/TEST_STRATEGY.md`, `docs/REQUIREMENT_TRACEABILITY.md`, `docs/RESUME_OUTPUT_STRUCTURE.md`, `CLAUDE.md`) plus `docs/V2_REUSE_AUDIT.md` and `docs/QUALITY_BENCHMARK.md` are mutually consistent as of the last verification — checked by direct cross-reading, not assumed.
- The four candidate source documents referenced by `bootstrap_candidate_memory` on `main` (`docs/AC/AC-MEMORY_PROFILE.md`, `AC-profile_english.md`, `AC-profile_german.md`, `docs/CANDIDATE_MEMORY_SNAPSHOT.md`) are present and intact on `cvbuild2`.

## In Progress

- Nothing. No application code, Django project, or migrations exist yet on `cvbuild2`.

## Known Issues / Risks

- No Django project, `Makefile`, or `.env.example` exists yet on `cvbuild2` — establishing them is M1 scope (see `docs/IMPLEMENTATION_PLAN.md` M1, `docs/V2_REUSE_AUDIT.md` §3 item 1). Do not assume any local dev command exists until M1 creates it.
- Two non-blocking implementation choices remain open, to be settled during their own milestones rather than now: whether `ExperienceSlot` metadata is copied at creation or resolved by reference to its source engagement record (M3A); exact stored enum naming for the AJ requirement taxonomy (M4).
- The `CandidateContextSnapshot` (M3B) and APS (M5) components have zero precedent on `main` and are the highest-uncertainty items in the plan — see `docs/V2_REUSE_AUDIT.md` §4.

## Verification

Commands used to verify the state above:

```
git branch --show-current
git log -5 --oneline --decorate
git status --short
```

No automated test suite exists yet — no Django app has been created on `cvbuild2`. Once M1 exists, this section should record the actual test command(s) run and their results (pass/fail counts), not just "tests pass."

## Last Completed Handover

- Outgoing: Claude Code, this session — architecture closure (M0.2 initial pass + follow-up pass) and multi-agent handover protocol establishment (`AGENTS.md`, `docs/ENGINEERING_RULES.md`, `docs/HANDOVER_PROTOCOL.md`, `docs/MILESTONE_COMPLETION_CHECKLIST.md`, this file).
- This is informational only — do not make correctness dependent on which tool wrote this. Verify the repository directly per `docs/HANDOVER_PROTOCOL.md` §B regardless of who the outgoing agent was.

## Next Recommended Action

1. Begin M1 (Foundation and Reuse Audit implementation) per `docs/IMPLEMENTATION_PLAN.md` and the closed `JobApplication`/`StageRun`/`JobApplicationStageState` design in `docs/ARCHITECTURE.md` §5.
2. Cherry-pick `main` files per `docs/V2_REUSE_AUDIT.md`'s recommended reuse order, file-by-file with tests, never by bulk merge.
3. Use `docs/MILESTONE_COMPLETION_CHECKLIST.md` before declaring M1 complete.

## Working Tree Expectations

Clean except for local, intentionally-untracked files that must never be committed: `.env`, `.venv/`, `.claude/`, `.DS_Store`. If `git status --short` shows anything else, investigate before proceeding — see `docs/HANDOVER_PROTOCOL.md` §B.

## Full Decision and Reuse History

This file intentionally does not restate project history. For the full list of closed architectural decisions, see `docs/DECISIONS.md` (as of this update: V2-D001 through V2-D035). For the full `main`-branch reuse classification, see `docs/V2_REUSE_AUDIT.md`.
