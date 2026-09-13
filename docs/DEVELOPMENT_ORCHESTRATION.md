# CVBuilder External Development-Agent Orchestration

**Status:** Implemented development infrastructure (V2-D045)
**Scope:** repository engineering only; never product-runtime orchestration

## Boundary

`tools/dev_orchestrator` supervises coding agents that work on CVBuilder. It does not run the resume
pipeline, import Django or `llm_provider`, or replace PostgreSQL product workflow state. Its state is
local operational metadata under `.orchestration/runs/`; raw run and worktree directories are ignored.

The implementation uses the Python standard library plus PyYAML, already present through the existing
development toolchain and now declared directly because the operator-facing configuration is genuine,
editable YAML. Unknown keys, roles, adapters, permissions, states, handoff statuses/verdicts, and
non-positive limits fail closed. The public bootstrap commit's final character is represented with a
JSON Unicode escape in the contract template solely to avoid a high-entropy false positive; JSON
decoding restores the exact SHA.

## Roles and isolation

- Orcha: Codex, read-only, prepares/clarifies/corrects/closes against repository evidence.
- Implementer: Cursor CLI in a dedicated implementation worktree. Write mode is refused unless the
  request identifies that permission profile and the controller has verified isolation.
- Reviewer: fresh Codex invocation in a different audit worktree. It starts read-only. If that audit
  requests an adversarial test, a second invocation may write only on a safety-verified audit branch;
  the controller rejects non-test changes and never transfers the resulting commit automatically.
- Claude orchestrator/reviewer: disabled placeholders. Disabled selection performs no binary lookup,
  authentication check, or subprocess launch.

Adapters use `subprocess` argument arrays, never `shell=True`. They stream stdout/stderr into separate
sanitized logs, parse NDJSON events, capture session IDs/final messages/handoffs/exit codes, enforce a
timeout, emit heartbeats, and terminate the process group on timeout. Agent subprocesses receive a
small environment allowlist rather than inherited API keys or credentials. Codex flags are based on
the installed `codex exec --help`; Cursor uses documented print + `stream-json` mode and session resume.

For Cursor, the controller treats a complete schema-shaped `assistant` event as the authoritative
handoff candidate and immediately serializes it into the invocation's durable `.final.json` file.
A later presentation-oriented `result` event containing a truncation marker cannot overwrite that
file. Malformed or genuinely incomplete assistant output remains rejected. Structured redaction is
performed before NDJSON persistence so sanitization cannot corrupt the JSON framing.

## Durable state

Each run contains `manifest.json`, atomic `state.json`, JSON/Markdown phase contracts,
`context-manifest.json`, append-only `events.jsonl`, structured handoffs, deterministic evidence, and
sanitized logs. State updates fsync a temporary file and atomically replace `state.json`; a crash cannot
partially write a false `COMPLETED` state.

An escalated run may receive a strictly validated, append-only operator decision under its existing
`handoffs/` directory. The original phase contract is never rewritten. Additional allowed paths are
applied only as an in-memory effective-contract amendment for that run. Agent Orcha receives the
original contract, amendment, prior structured question, current worktree evidence, unresolved
verification failure, and remaining acceptance criteria; its exact correction prompt is persisted
under the run's `prompts/` directory with a SHA-256 identity before the saved implementer session is
resumed in the existing worktree.

If an independent reviewer invocation fails before producing an audit handoff, `resume` may retry the
same audit only after a committed, bounded orchestration-tooling repair. The controller requires the
validated implementation and audit worktrees to remain registered, clean, and pinned to the recorded
result SHA; it reuses those worktrees and emits a durable `reviewer_recovery_retry` event. Codex output
schemas declare explicit JSON types for every `const` and `enum` constraint so the reviewer API can
validate them before the audit starts.

If the implementer commits a correction but its final message is missing or invalid structured output
(for example schema-valid JSON followed by prose), `resume` may retry only the saved Cursor session
after a committed, bounded orchestration-tooling repair. The controller requires the implementation
worktree to remain registered and clean, its HEAD to be a descendant of the approved base and different
from the prior recorded result, the saved implementer session to exist, and no conflicting handoff for
the current correction cycle. It persists an exact no-file-change prompt that demands schema-only JSON
for the already-committed HEAD (with the full base-to-result file list and required tests), emits
`implementer_strict_output_retry`, and continues through the normal evidence boundary. Parsing and
schema validation are not relaxed; the malformed prior message is never accepted as a handoff.
Strict-output prompts list only executable `required_tests` (`make `, `git `, `.venv/` prefixes) inside
`test_commands` instructions; narrative verification expectations stay narrative-only.

If evidence validation rejects a schema-valid implementer handoff solely because `test_commands`
included an unapproved non-executable narrative command, `resume` may retry after a committed tooling
repair without changing the product commit. The rejected handoff remains append-only; a distinct
corrected handoff artifact is written and selected through `active_implementer_handoff_path`. The
implementation worktree must stay registered, clean, and pinned at `result_sha`, the returned
`result_sha` must match that pin, and a second resume reuses the corrected artifact instead of
re-prompting. Reused corrected artifacts must also match the current run identity (`IMPLEMENTED`,
matching `base_sha` and pinned `result_sha`) or the controller fails closed. Other evidence failures
remain operator escalations.

The explicit states are `PREPARING`, `AWAITING_PHASE_APPROVAL`, `IMPLEMENTING`,
`IMPLEMENTER_QUESTION`, `ORCHA_DECISION`, `VALIDATING_IMPLEMENTATION`, `AUDITING`,
`CORRECTION_REQUIRED`, `ORCHA_CORRECTION_CONTRACT`, `CORRECTING`, `CLOSURE_REVIEW`, `COMPLETED`,
`BLOCKED`, `OPERATOR_ESCALATION`, and `ABORTED`. Invalid transitions are rejected.

Question cycles stop at five. Corrections stop after three. A stable finding that remains open through
two correction attempts escalates. Agent failure without a valid recoverable handoff, unexpected Git
movement, governance ambiguity, secrets, destructive migration ambiguity, or an audit-test transfer
also requires the operator. Audit-test creation is isolated; transfer into the implementation branch
remains an explicit operator gate.

A reviewer-side failed test command can never support `PASS`. When the reviewer instead returns
`CORRECTION_REQUIRED`, the controller preserves the failed command alongside the already-validated
implementation evidence and routes the findings through the normal Agent Orcha correction cycle.
This allows an isolated reviewer to report a genuine product finding even when its worktree cannot
access local secrets such as database credentials; successful reviewer commands are still rerun by
the controller.

The controller maps structured role outcomes to the requested orchestration decisions: an answered
question continues, an accepted audit correction produces `CORRECT`, successful closure produces
`COMPLETE`, and unrecoverable or authority-expanding conditions produce `BLOCKED` or
`OPERATOR_ESCALATION`.

## Deterministic evidence

Before audit, the controller verifies the base/result objects, ancestry, candidate worktree HEAD and
cleanliness, commit range, changed/untracked files, allowed/prohibited paths, unauthorized
`requirements.md`/decision changes, deleted or weakened tests, and verification results. It
independently re-executes the allowlisted reported commands with argument arrays and compares their
real exit codes with the handoff instead of trusting the agent's summary.
Allowed Git verification forms are only `git status --short` and read-only `git diff --check`
with exactly this grammar: zero endpoints; one revision endpoint; two revision endpoints; or
exactly one `A..B` / `A...B` token whose two sides contain no further range delimiters. Each
endpoint must resolve to a commit via `git rev-parse` in the candidate worktree before the
command is accepted or executed; filesystem paths and nonexistent revisions are rejected, and
accepted forms are executed with a trailing `--` so tokens cannot be reinterpreted as pathspecs.
Shell operators, leading-dash option injection, nested/empty/multiple-range forms, other git
subcommands, and write-capable commands remain rejected.
Malformed handoffs, nonexistent or wrong-base SHAs, failed commands, dirty worktrees, and prohibited
changes cannot reach closure. The reviewer receives the contract and candidate Git state, not the
implementer's conversational history.

No controller path merges, pushes, rebases, resets, force-updates, deletes a branch/worktree, or stashes
changes. `COMPLETED` therefore means “eligible for an operator merge decision,” never “merged.”

## tmux

`attach` creates three observational panes (Orcha, Implementer, Reviewer) that render filtered durable
events. Agents do not communicate through keystrokes or pane buffers. `state.json` and `events.jsonl`
remain authoritative if tmux is closed or detached.

## M3A contract

The versioned template is `.orchestration/contracts/M3A.json`; it is derived from the current M3A
definition, not the abandoned M3A branch. It distinguishes:

- product baseline: `437b179490b01697366ba70d75b6c72a6c83a7a4`;
- orchestration tooling SHA: resolved after this bootstrap is committed;
- future M3A implementation base: the clean orchestration-enabled HEAD captured by `plan`.

Planning copies and validates the template into the run directory. A change to HEAD after planning
invalidates approval. M3A itself is not implemented by this bootstrap.
