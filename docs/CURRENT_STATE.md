# Current State

**Do not treat this file as self-certifying.** Verify Git and tests directly before relying on it.

This root-checkout `docs/CURRENT_STATE.md` describes the **root repository checkout** only.
Controller agent prompts also require durable `.orchestration` run state plus the
**worktree-local** `docs/CURRENT_STATE.md` for the request worktree; never substitute this
root file for an implementation/audit worktree copy.

## Repository State

- Product baseline: `cvbuild2` commit `437b179490b01697366ba70d75b6c72a6c83a7a4` (verified M2).
- Development-orchestration bootstrap / tooling line: `buildwithAgent` (and related tooling
  branches), descended from that baseline; verify the current commit with `git rev-parse HEAD`.
- The abandoned M3A line ending at `02bbc5c` and listed predecessor commits is excluded.
- `main` remains the incompatible V1 legacy line (V2-D038).
- Last updated: 2026-09-19.

## Current Task / Live Run

- External development-agent orchestration (V2-D045) is implemented under `tools/dev_orchestrator`
  and has been used for a live supervised M3A attempt.
- Durable run `m3a-d2c0cc48b6aa` exists under `.orchestration/runs/` and is currently in
  `OPERATOR_ESCALATION` (not completed, not merged).
- That run’s implementation worktree candidate result is
  `969283968a6772cc1b9f7930241a8a0a33c7e857`; open audit findings remain and require operator /
  controller recovery before any merge decision.
- M3A product work is **not** accepted as complete and must not be treated as passed.

## Verified Working

- M1/M2 product baseline remains the accepted product line; confirm with `make verify` on the
  checkout under test.
- External controller: strict YAML config; Codex/Cursor/disabled-Claude/fake adapters;
  atomic state; sanitized events/logs; structured phase/implementer/audit/closure schemas; Git evidence;
  isolated worktrees; bounded questions/corrections; safe resume/abort; three-pane observational tmux;
  per-run `supplemental-audits/` sibling for non-pipeline audit evidence; worktree-local startup
  identity (branch/`DETACHED`, HEAD, durable run ID/state from `state.json`, local
  `docs/CURRENT_STATE.md` path/hash constrained inside the request worktree) on every agent request.
- `doctor` qualifies installed tooling; Cursor authentication and live model availability remain
  operator prerequisites and can change over time—re-run `doctor` rather than trusting historical
  login notes.

## Known Limits / Operator Actions

- Live Codex/Cursor invocations have occurred for run `m3a-d2c0cc48b6aa`; that run is still
  escalated with open findings and is not merge-ready.
- V2-D031 leaves `ExperienceSlot` copy-versus-reference as an M3A implementation detail. The contract
  permits the smallest approach only if source linkage, operator selection, and static ownership remain
  intact; any broader architecture change still requires escalation.
- Adversarial audit-test commits require a separate operator-approved write/transfer action; they are
  detected and never transferred automatically.
- Independently commissioned supplemental audits belong under `supplemental-audits/` and do not drive
  the state machine until translated through an authorized schema-valid controller path.

## Next Recommended Action

Continue recovery of `m3a-d2c0cc48b6aa` on the same run ID only through the documented
`resume` / operator-decision paths after reviewing durable state, events, and handoffs. Do not create
a second M3A run. Do not merge or push from orchestration tooling.
