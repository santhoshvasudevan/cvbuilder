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
