# Current State

**Do not treat this file as self-certifying.** Verify Git and tests directly before relying on it.

This root-checkout `docs/CURRENT_STATE.md` describes the **root repository checkout** only.
Controller agent prompts also require durable `.orchestration` run state plus the
**worktree-local** `docs/CURRENT_STATE.md` for the request worktree; never substitute this
root file for an implementation/audit worktree copy.

## Repository State

- Product baseline: `cvbuild2` commit `437b179490b01697366ba70d75b6c72a6c83a7a4` (verified M2).
- Development-orchestration tooling line: `buildwithAgent`; this integration branch starts from
  `1f9fe6ef45a2f06ba88d6481bfce1b7d29328a48` and merges the preserved M3A candidate for correction.
- Preserved M3A implementation branch: `agent/m3a-d2c0cc48b6aa/implementation` at
  `969283968a6772cc1b9f7930241a8a0a33c7e857` (run `m3a-d2c0cc48b6aa`).
- The abandoned M3A line ending at `02bbc5c` and listed predecessor commits remains excluded.
- `main` remains the incompatible V1 legacy line (V2-D038).
- Last updated: 2026-09-21.

## Current Task / Live Run

- The durable run `m3a-d2c0cc48b6aa` remains in terminal `OPERATOR_ESCALATION`; it must not be
  resumed, advanced, or modified. Its candidate branch and three worktrees remain preserved.
- The candidate is integrated only on `m3a-integration` so a new bounded corrective slice can close
  deferred findings `AUDIT-002` and `CLAUDE-M3A-002`. M3A is not yet accepted or merged into
  `buildwithAgent`.
- M3B `CandidateContextSnapshot` has not started; the `candidate_context` app is intentionally absent.

## Verified Working

- M1/M2 product baseline remains the accepted product line; confirm with `make verify` on the
  checkout under test.
- External controller: strict YAML config; Codex Orcha, Cursor Implementer, enabled Claude Reviewer,
  disabled Codex reviewer rollback, and fake adapters;
  atomic state; sanitized events/logs; structured phase/implementer/audit/closure schemas; Git evidence;
  isolated worktrees; bounded questions/corrections; safe resume/abort; three-pane observational tmux;
  per-run `supplemental-audits/` sibling for non-pipeline audit evidence; worktree-local startup
  identity (branch/`DETACHED`, HEAD, durable run ID/state from `state.json`, local
  `docs/CURRENT_STATE.md` path/hash constrained inside the request worktree) on every agent request.
- `doctor` qualifies installed tooling; Cursor authentication and live model availability remain
  operator prerequisites and can change over time—re-run `doctor` rather than trusting historical
  login notes.
- `candidate_memory` Django app with provenance-bearing source ingestion, MemoryClaims/supports,
  unresolved MemoryConflicts, CandidateProfile preferences, CareerEngagement roster,
  StaticResumeProfile, and sequenced ExperienceSlot collection (copy-on-create + source FK).
- Operator-controlled ExperienceSlot workspace (server-rendered) plus admin-backed explicit
  three-engagement selection action — no automatic primary-slot selection path.
- Deterministic HARD_INTEGRITY validator requiring exactly three unique active primary slots
  with sequences 1, 2, and 3; static metadata replacement attempts are rejected.
- `docs/CANDIDATE_MEMORY_SNAPSHOT.md` is excluded from ingestion (path + generated markers).
- Fresh disposable PostgreSQL migration apply/no-op/unapply-reapply covered by
  `candidate_memory.tests.test_migrations`.

## Known Limits / Operator Actions

- `AUDIT-002` remains open/deferred: source-document and claim-support provenance described as
  immutable is still ordinarily mutable/deletable and source deletion cascades support evidence.
- `CLAUDE-M3A-002` remains open/deferred: conflict detection is candidate-specific and misses the
  known Maruti start-date contradiction.
- Certifications/languages are stored only on StaticResumeProfile as static JSON; no LLM path
  generates them in M3A.
- ExperienceSlot copy-versus-reference chose the smallest copy-on-create approach with retained
  CareerEngagement linkage (V2-D031 permitted detail).
- Adversarial audit-test commits still require a separate operator-approved write/transfer action.
- Independently commissioned supplemental audits belong under `supplemental-audits/` and do not drive
  the state machine until translated through an authorized schema-valid controller path.
- Operator alone decides whether to merge the corrected M3A result into `buildwithAgent`.

## Next Recommended Action

Plan and approve a new bounded controller run from this integration branch to correct `AUDIT-002`
and `CLAUDE-M3A-002`; do not resume the terminal M3A run. Merge into `buildwithAgent` only after the
corrective candidate passes the controller and the operator authorizes integration.
