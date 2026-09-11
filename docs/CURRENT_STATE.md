# Current State

**Do not treat this file as self-certifying.** Verify Git and tests directly before relying on it.

## Repository State

- Product baseline: `cvbuild2` commit `437b179490b01697366ba70d75b6c72a6c83a7a4` (verified M2).
- Development-orchestration bootstrap: `buildwithAgent` / `2fea4b43ffdb74dec314f68086d172db8570a95e`.
- M3A implementation worktree branch: `agent/m3a-d2c0cc48b6aa/implementation` (run `m3a-d2c0cc48b6aa`).
- The abandoned M3A line ending at `02bbc5c` and listed predecessor commits remains excluded.
- `main` remains the incompatible V1 legacy line (V2-D038).
- Last verified date: 2026-09-11.

## Current Task

- M3A Candidate Knowledge and StaticResumeProfile: **implemented in this worktree** pending
  independent audit and operator acceptance/fast-forward into `cvbuild2`.
- M3B CandidateContextSnapshot: not started (`candidate_context` app intentionally absent).

## Verified Working

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

- Certifications/languages are stored only on StaticResumeProfile as static JSON; no LLM path
  generates them in M3A.
- ExperienceSlot copy-versus-reference chose the smallest copy-on-create approach with retained
  CareerEngagement linkage (V2-D031 permitted detail).
- Adversarial audit-test commits still require a separate operator-approved write/transfer action.
- Operator alone decides whether to fast-forward the accepted M3A result into `cvbuild2`.

## Next Recommended Action

Independent audit of this M3A candidate against `.orchestration/contracts/M3A.json`. Do not merge
or push from orchestration tooling.
