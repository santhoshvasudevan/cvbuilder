# Current State

**Do not treat this file as self-certifying.** Verify Git and tests directly before relying on it.

## Repository State

- Product baseline: `cvbuild2` commit `437b179490b01697366ba70d75b6c72a6c83a7a4` (verified M2).
- Development-orchestration bootstrap branch: `buildwithAgent`, descended directly from that baseline;
  verify the current bootstrap commit with `git rev-parse HEAD`.
- The abandoned M3A line ending at `02bbc5c` and listed predecessor commits is excluded.
- `main` remains the incompatible V1 legacy line (V2-D038).
- Last verified date: 2026-09-11.

## Current Task

- External development-agent orchestration bootstrap (V2-D045): implemented and deterministically
  verified outside Django under `tools/dev_orchestrator`.
- M3A: **not implemented and not started live**. A clean versioned contract exists at
  `.orchestration/contracts/M3A.json`; dry-run planning has been exercised with no agent call.

## Verified Working

- M1/M2 product baseline plus bootstrap: `make verify` passes 271 tests (231 existing M1/M2 + 40
  external orchestration tests), with Django checks, migration drift, and Ruff all clean.
- External controller: strict YAML config; Codex/Cursor/disabled-Claude/fake adapters;
  atomic state; sanitized events/logs; structured phase/implementer/audit/closure schemas; Git evidence;
  isolated worktrees; bounded questions/corrections; safe resume/abort; three-pane observational tmux.
- Non-live `doctor` qualifies installed Codex CLI capabilities/authentication, Git/tmux/jq, worktree
  safety, and redaction. Cursor CLI is installed but reported `Not logged in` on 2026-09-11; this is a
  live-run prerequisite, not a deterministic test failure. Claude remains disabled and unchecked.

## Known Limits / Operator Actions

- No real Codex/Cursor/Claude development-agent invocation has been made by this bootstrap.
- Configured Codex model names are syntactically recorded but model availability was not tested with a
  paid/live call. Cursor model `auto` likewise was not queried because Cursor is not authenticated.
- V2-D031 leaves `ExperienceSlot` copy-versus-reference as an M3A implementation detail. The contract
  permits the smallest approach only if source linkage, operator selection, and static ownership remain
  intact; any broader architecture change still requires escalation.
- Adversarial audit-test commits require a separate operator-approved write/transfer action; they are
  detected and never transferred automatically.
- The operator must fast-forward `cvbuild2` to the accepted bootstrap commit before planning the first
  live M3A run, then authenticate Cursor and re-run `doctor`.

## Next Recommended Action

Independently review the bootstrap commit. If accepted, operator-fast-forward `cvbuild2`, authenticate
Cursor, run the commands in `docs/DEVELOPMENT_RUNBOOK.md`, inspect/approve the generated M3A contract,
and launch M3A as the first supervised live run. No merge or push has been performed by the tooling.
