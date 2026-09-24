# Orchestration Handover

**Dated:** 2026-09-24T08:57Z. Written by the outgoing orchestrator (Claude) for the incoming
orchestrator (Codex). After this session, Claude becomes the controller's reviewer role; Codex
orchestrates. Verify everything below directly — this file is a snapshot, not a live source.

## Branch and HEAD

- Branch: `buildwithAgent`. This file, the contract retarget, and everything else in this handover
  are committed and pushed to `origin/buildwithAgent` before this session ends — run `git log -1`
  and `git status` now to see the actual current HEAD and confirm the tree is clean; do not trust
  a specific SHA written here as still current.
- If the tree is *not* clean, or local HEAD is ahead of `origin/buildwithAgent`, something changed
  after this file was committed — investigate before continuing; do not assume it is safe to
  proceed.

## M3B-S1: delivered, not landed

Read `docs/CURRENT_STATE.md` first — it has the full table of all three attempted candidates, why
each stopped, and the exact next action. Summary: the `candidate_context` app,
`CandidateContextSnapshot` model, and `direct_match` bucket have been implemented correctly three
times (content-identical each time). The furthest candidate, `541c4e7` (tag
`preserved/m3b-s1-a6628234bbc3-audit-passed`), passed implementation, evidence collection, *and* an
independent reviewer audit (verdict `PASS`) — it failed only in `CLOSURE_REVIEW`, on a genuine
tooling bug (see `docs/ORCHESTRATION_BACKLOG.md` item 10): the `orcha_closure` adapter's provider
(Codex, strict structured-output mode) rejects `closure-narrative.schema.json`'s
`additionalProperties: true` at the API level, before the model ever runs.

**Do this first, before anything else on M3B, in this exact order:**

1. Decide and implement a fix for the closure-narrative schema conflict (backlog item 10 describes
   two candidate approaches with a real trade-off — this is an operator/design decision, not
   something to pick unilaterally; get it confirmed). The *confirmed* failure is
   `closure-narrative.schema.json` only, routed through `orcha_closure` (Codex, strict
   structured-output mode). `implementer-narrative.schema.json` shares the same
   `additionalProperties: true` pattern and would likely hit the identical wall if it were ever
   routed through a strict-mode provider, but that has not been reproduced — the implementer role
   is Cursor-routed, a different adapter, and was not affected this session.
2. Commit and push that tooling fix, then confirm the repository is otherwise idle (see
   Operational preflight in `docs/DEVELOPMENT_ORCHESTRATION.md` — Docker/Postgres up,
   `pgrep -fl dev_orchestrator` empty, memory pressure normal) **before** opening a new run.
3. **Do not `resume` any of the runs in the table below** — `OPERATOR_ESCALATION` is terminal;
   none of `resume`'s three repair helpers cover a `CLOSURE_REVIEW`/`orcha_closure` failure (see
   `docs/DEVELOPMENT_ORCHESTRATION.md`'s Operator verbs section), and it will return the run
   unchanged. Instead run a **fresh** plan: `validate-config` → `doctor` → `plan --phase M3B-S1`
   → inspect the new run's contract JSON → `approve --run-id <new-run-id>` →
   `run --phase M3B-S1 --run-id <new-run-id>`. The contract's `in_scope` STARTING POINT entry
   already points at `541c4e7` (retargeted this session, after this file was first drafted — check
   `.orchestration/contracts/M3B-S1.json` directly rather than trusting this sentence to stay
   current), so the fresh implementer session can reuse that content rather than re-deriving it,
   but it still owns, must independently verify, and will be independently audited on the result —
   `541c4e7`'s own prior `PASS` verdict belongs to a different, abandoned run and carries no
   standing in a new one.
4. If `record-reverification-decision` or `record-post-result-decision` is needed again on the
   fresh run (a database-outage-style mismatch, or the same two milestone-boundary carve-out files
   respectively), see `docs/DEVELOPMENT_ORCHESTRATION.md`'s Operator verbs section for exact
   preconditions and payload shape; there is no example payload file left behind from this session
   to copy — construct fresh ones per that section.

## Remaining M3B slices (S2–S5) — not formally planned

`docs/IMPLEMENTATION_PLAN.md`'s M3B section lists five context-retrieval buckets as its scope:
`direct_match` (S1, delivered above), `differentiators`, `career_narrative`, `foundations`,
`gaps_constraints`, plus an "editable context UI" and "context token reporting" requirement not
yet assigned to any slice. No S2–S5 contract JSON exists yet. A natural (but *not yet decided or
authored*) one-bucket-per-slice breakdown:

- **S2 — differentiators**: retained context items not required to map one-to-one to a job
  requirement.
- **S3 — career_narrative**: full career chronology, not only the latest role.
- **S4 — foundations**: retained foundational context (not defined further by
  `docs/IMPLEMENTATION_PLAN.md` beyond "retained").
- **S5 — gaps_constraints**: retained gaps/constraints context.

The editable context UI and token-reporting requirements have no home in this breakdown yet —
whoever plans S2 should decide whether either belongs in a slice or needs its own. Do **not** plan
S2 until S1 has actually landed; nothing this session did should change what S2 assumes, since
every attempted candidate matched the M3B-S1 contract's spec throughout.

## Every M3B-S1 run ID and its state

| Run ID | State | Result SHA | Notes |
|---|---|---|---|
| `m3b-s1-28369cfc48b1` | `AWAITING_PHASE_APPROVAL` | — | Pre-dates this session; never approved/run. |
| `m3b-s1-a8da7a92afed` | `OPERATOR_ESCALATION` | — | Escalated via `QUESTION` cycle (job_applications boundary-file gap); superseded. |
| `m3b-s1-4d33936a73b9` | `OPERATOR_ESCALATION` | — | Contract-defect era; superseded. |
| `m3b-s1-02669212deb1` | `OPERATOR_ESCALATION` | — | Holds the strengthened `test_local_postings_exclusion` commit (`5faa45c`), later reused. |
| `m3b-s1-0e1a799bdca6` | `OPERATOR_ESCALATION` | `ccd4736` | Most complete pre-governance-fix attempt; superseded (this exact commit touches `docs/DECISIONS.md`, now out of scope — do not reuse it directly). |
| `m3b-s1-d323a242d9ff` | `OPERATOR_ESCALATION` | — (never recorded) | Controller process OOM-killed mid-run; implementer finished and committed `1f63825` independently. Tag `preserved/m3b-s1-d323a242d9ff-orphaned-implementation`. |
| `m3b-s1-1760a171e5be` | `AWAITING_PHASE_APPROVAL` | — | A `plan --dry-run` invocation this session; never approved/run. |
| `m3b-s1-9aebc939eb4f` | `OPERATOR_ESCALATION` | `c701890` | Reached `VALIDATING_IMPLEMENTATION` cleanly; abandoned for a self-inflicted tooling-HEAD-pin mismatch (see backlog item 4/5). Tag `preserved/m3b-s1-9aebc939eb4f-verified-candidate`. |
| `m3b-s1-a6628234bbc3` | `OPERATOR_ESCALATION` | `541c4e7` | **Furthest progress**: audit verdict `PASS`. Failed only in `CLOSURE_REVIEW` (backlog item 10). Tag `preserved/m3b-s1-a6628234bbc3-audit-passed`. |

None of these runs should be resumed — see step 3 in the section above for the concrete
alternative (a fresh plan referencing the preserved candidate).

## Retained worktrees, branches, and tags — and why

All of the following are preserved deliberately; none should be deleted without first confirming
its content is no longer needed:

- `.orchestration/worktrees/m3b-s1-{02669212deb1,0e1a799bdca6,4d33936a73b9,9aebc939eb4f,a6628234bbc3,a8da7a92afed,d323a242d9ff}/implementation` (and `m3b-s1-a6628234bbc3/audit-00`) — one worktree per run above; each still checked out at its final commit.
- Branches `agent/m3b-s1-*/implementation` for each run ID above — reachable independently of the worktrees.
- Tags: `preserved/m3b-s1-d323a242d9ff-orphaned-implementation` (→ `1f63825`),
  `preserved/m3b-s1-9aebc939eb4f-verified-candidate` (→ `c701890`),
  `preserved/m3b-s1-a6628234bbc3-audit-passed` (→ `541c4e7`) — each an explicit backup anchor,
  independent of its branch ref, for a candidate that reached meaningful verification depth before
  its run was abandoned for a non-content reason.
- M3A-era worktrees/branches (`m3a-c1-*`, `m3a-d2c0cc48b6aa/*`) predate this session and are
  unrelated to M3B-S1; M3A is fully landed (see `docs/CURRENT_STATE.md`). **Do not prune these** —
  no pruning decision or verification was made this session; treat "M3A is landed" as a fact about
  the product, not as authorization to delete its historical worktrees.
- Two other registered worktrees exist that are unrelated to M3A or M3B and were not created by
  this session's controller runs: `~/.herdr/worktrees/cvbuilder/m3b-s1-governance-fix` (clean,
  contained, already merged into `buildwithAgent` — likely inert, but not verified as safe to
  remove this session) and `.claude/worktrees/m7-ux-followup` (**dirty** — has an untracked `.venv`
  — do not touch or clean this one without understanding what it holds; it was not investigated
  this session and may be another agent's or the operator's own in-progress work).

## Reviewer routing

Routed to **Claude** (`agents.reviewer: <<: *claude_reviewer`, `.orchestration/config.yaml`),
confirmed via `doctor` including a live dry audit passing `validate_audit_response()`. Codex
remains fully defined as `codex_reviewer` (`enabled: false`) and was independently re-verified via
`doctor` in the same session (also a passing live dry audit) before this switch.

**Rollback** (three coordinated edits in `.orchestration/config.yaml`, not truly "one line"):
swap `agents.reviewer`'s merge target from `<<: *claude_reviewer` back to `<<: *codex_reviewer`,
and swap each anchor's own `enabled` flag (`codex_reviewer: enabled: true`,
`claude_reviewer: enabled: false`) for consistency with the documented convention — then re-run
`doctor` to re-qualify before trusting the switch. This affects the `reviewer` role only.
`orcha`/`orcha_closure` remain Codex-routed regardless of this switch, and switching the reviewer
does **not** fix or touch backlog item 10 (the closure-narrative schema conflict is entirely on
the `orcha_closure` role, unaffected by which adapter serves `reviewer`).

The Claude adapter's draft-07 schema-compatibility shim
(`tools/dev_orchestrator/adapters/claude_schema.py`) was re-verified empirically this session
against the currently-installed Claude Code build (2.1.267, unchanged): a draft-2020-12
`$schema` declaration is still rejected outright by `--json-schema`; draft-07 is accepted and
correctly enforces nested `$defs`/`$ref` constraints. **Do not remove this shim** without
re-running that probe against whatever Claude Code version is installed at the time.

## Backlog, ranked by runs already cost

See `docs/ORCHESTRATION_BACKLOG.md` for full proposals (nothing there is built).
**Item 10 is the immediate, do-this-now unblocker** (see the M3B-S1 section above) — it is the
only thing standing between the audit-passed candidate `541c4e7` and landing, and nothing else in
this list should be worked before it. Everything below is the *strategic* backlog, ranked by
direct evidence of cost this session, to work through **after** S1 lands:

1. **`OPERATOR_ESCALATION` terminal-state classification (backlog item 4)** — 6 re-plans forced;
   3 narrow verbs (`record-decision`, `record-post-result-decision`,
   `record-reverification-decision`) have accumulated in place of fixing the underlying gap.
2. **`phase-contract.schema.json` shape-only validation (item 1)** — 4 separate contract defects
   reached live runs (job_applications boundary coverage, `required_tests` shape, `required_documentation_updates`
   shape, governance-gate omission).
3. **The tooling-HEAD-pin's missing acknowledgement path (item 5)** — 1 run abandoned
   (`m3b-s1-9aebc939eb4f`), directly caused by this session's own sequencing mistake (merging
   tooling mid-run); see the Operational preflight section of `docs/DEVELOPMENT_ORCHESTRATION.md`
   for the worked example and the standing rule it produced.
4. **`_validate_verification_argv` running at evidence time, not plan time (item 2)** — same root
   cause as item 2 above; would have caught the `required_tests` shape defect before any agent ran.
5. **Whether the Phase 1 verification lock covers agent-invoked test runs (item 8)** — contributed
   to (though probably was not the sole cause of) the OOM incident's Postgres contention;
   unresolved, needs a direct code-path check.
6. **`authorized_governance_changes` dead code (item 3)**, **the accepted single-use-marker race
   (item 7)**, **routing the boundary carve-out to a mandatory reviewer item instead of an
   escalation (item 6)**, **AC-8-style checks proving constants match without proving use (item
   9)** — no run has been lost to any of these yet; lower-ranked on direct evidence, not on
   importance.

## Agent invocation totals across all M3B-S1 runs (this session)

Summed from every `agent_invocation_metrics` event across all `m3b-s1-*` run directories
(`.orchestration/runs/m3b-s1-*/events.jsonl`). `IMPLEMENTER` is always Cursor; `ORCHA` and
`REVIEWER` were always Codex-routed for every run in this table (the switch to Claude for
`reviewer` happened after `m3b-s1-a6628234bbc3` had already completed its audit).

**Cursor (`IMPLEMENTER`, 5 invocations with recorded metrics — one run, `m3b-s1-d323a242d9ff`,
never got the chance to record its metrics event before the controller was OOM-killed):**
- input tokens: 658,496
- cached input tokens: 4,133,760
- output tokens: 52,372
- tool calls: 294

**Codex (`ORCHA` + `REVIEWER` combined, 5 invocations with recorded metrics):**
- input tokens: 232,888 (`ORCHA`) + 1,184,181 (`REVIEWER`) = 1,417,069
- cached input tokens: 112,384 (`ORCHA`) + 1,108,992 (`REVIEWER`) = 1,221,376
- output tokens: 2,713 (`ORCHA`) + 4,777 (`REVIEWER`) = 7,490
- reasoning output tokens: 1,302 (`ORCHA`) + 1,633 (`REVIEWER`) = 2,935
- tool calls: 4 (`ORCHA`) + 12 (`REVIEWER`) = 16

This is the baseline the user asked be recorded before planning the remaining M3B slices.
