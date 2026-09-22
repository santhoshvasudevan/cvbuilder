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
- Reviewer: fresh Claude Code invocation in a different audit worktree, using the CLI's
  `--json-schema` support against `audit-response.schema.json`. It starts read-only. If that audit
  requests an adversarial test, a second invocation may write only on a safety-verified audit branch;
  the controller rejects non-test changes and never transfers the resulting commit automatically.
- The prior Codex reviewer remains a disabled rollback profile. The Claude orchestrator remains a
  disabled placeholder; disabled selection performs no binary lookup, authentication check, or
  subprocess launch.

Adapters use `subprocess` argument arrays, never `shell=True`. They stream stdout/stderr into separate
sanitized logs, parse NDJSON events, capture session IDs/final messages/handoffs/exit codes, enforce a
timeout, emit heartbeats, and terminate the process group on timeout. Agent subprocesses receive a
small environment allowlist rather than inherited API keys or credentials. Codex flags are based on
the installed `codex exec --help`; Cursor uses documented print + `stream-json` mode and session
resume. Claude uses print mode, receives the prompt on stdin, requests JSON output constrained by the
audit schema, and normalizes its input/cache-read/output/thinking usage into the common invocation
metrics event. Read-only Claude invocations use `dontAsk` with an explicit allowlist for repository
inspection and the controller's deterministic Git/Python/Make checks; `Edit`, `Write`, and
`NotebookEdit` are explicitly denied. Operations outside that allowlist are denied without an
interactive prompt. Claude Code 2.1.267
lacks the draft 2020-12 meta-schema, so the adapter deep-copies the
canonical schema in memory and changes only its `$schema` declaration to draft-07 before invoking the
CLI. The canonical file remains unchanged and controller validation still uses the canonical
contract. A fail-closed keyword guard prevents this compatibility shim from silently downgrading
2020-12 constraints; remove it once Claude ships a 2020-12 validator.

The shared reviewer prompt states the controller's cross-field rule explicitly: `PASS` requires
every finding to be `CLOSED`, while any `OPEN` finding requires `CORRECTION_REQUIRED` or `BLOCKED`.
The prompt is provider-neutral and receives the effective contract assembled by the controller: the
immutable phase contract plus approved operator amendments, clarifications, and prior correction
prompts. Its test authority comes only from executable entries in that effective contract's
`required_tests`; adapter tool availability is capability, not permission to add test commands.
Live prompts never instruct a reviewer to override repository documentation, suppress findings, or
self-authorize commands. Historical-candidate proof scaffolding stays outside the live controller.
Because JSON Schema constrains structure rather than that semantic relationship, the controller may
make one bounded same-session repair request when those fields conflict. The request names the exact
violation, disables Claude tools, and requires the reviewer to preserve its audit, findings, statuses,
and test results while correcting only the verdict and summary. A missing session, any structural
schema error, or a second invalid response fails closed as before.

`doctor` performs the normal non-mutating environment checks and qualifies the configured reviewer
role against whichever adapter it is actually routed to. For Claude that is binary resolution,
version, structured-output CLI flags, and one minimal live dry audit. For Codex that is binary
resolution, machine-readable exec flags, login status, and one minimal live dry audit. Either dry
audit must pass both the JSON schema and `validate_audit_response()`. Automated tests continue to
use scripted adapters and never make this live call.

### Reviewer rollback

Both `claude_reviewer` and `codex_reviewer` profiles are defined; exactly one must have `enabled: true`
at a time. To switch the active reviewer, change the YAML merge line under `agents.reviewer` between
`<<: *claude_reviewer` and `<<: *codex_reviewer`, keep that selected profile enabled and the other
disabled, then re-run `doctor`. Doctor qualifies whichever adapter the role is routed to (including a
live dry audit through `validate_audit_response()`), and both directions are covered by automated
tests. Switching is a one-line merge change plus doctor re-qualification — not an untested claim that
"no controller code change is required."

For Cursor, the controller treats a complete schema-shaped `assistant` event as the authoritative
handoff candidate and immediately serializes it into the invocation's durable `.final.json` file.
A later presentation-oriented `result` event containing a truncation marker cannot overwrite that
file. Malformed or genuinely incomplete assistant output remains rejected. Structured redaction is
performed before NDJSON persistence so sanitization cannot corrupt the JSON framing.

## Durable state

Each run contains `manifest.json`, atomic `state.json`, JSON/Markdown phase contracts,
`context-manifest.json`, append-only `events.jsonl`, structured handoffs, deterministic evidence, and
sanitized logs. `handoffs/` is reserved for controller pipeline artifacts that match the controller
schemas and naming/validation expectations (implementer/audit/closure/operator-decision/correction
files). Independently commissioned Claude or other supplemental audits whose JSON intentionally uses
a different schema are stored under the sibling per-run `supplemental-audits/` directory created at
`plan` time. The sanctioned writer is
`python -m tools.dev_orchestrator record-supplemental-audit --run-id <run-id> --file <json-file>`:
it verifies durable `state.json` identity, accepts any JSON object without
`validate_audit_response`, writes append-only under `supplemental-audits/` (never `handoffs/`), and
emits `supplemental_audit_recorded` without transitioning or otherwise mutating `RunState`.
Supplemental audits are evidence only: they are never loaded by `validate_audit_response`, never
drive state transitions or closure, and become authoritative only after an authorized translation
into a schema-valid pipeline handoff. State updates fsync a temporary
file and atomically replace `state.json`; a crash cannot partially write a false `COMPLETED` state.

After every agent subprocess invocation, the existing event ledger receives one passive
`agent_invocation_metrics` record. It derives provider usage from the already-sanitized streamed
events and records the durable run ID and role, provider task ID (`thread_id` for Codex,
`request_id` for Cursor, or `session_id` for Claude), candidate SHA when known, UTF-8 prompt and
task-packet byte counts, normalized
input/cached-input/output/reasoning-output token counts, unique tool-call count, UTF-8 bytes of captured
completed tool results, retry reason, and correction count. Missing provider fields remain empty or
zero. The record does not alter prompts, schemas, role selection, retry decisions, or run state.

Every controller-created agent request begins with an explicit startup identity derived from the
request worktree (not the root checkout) and from the durable `state.json` loaded via `StateStore`
at request construction time (not a caller-supplied in-memory state): git branch or `DETACHED`,
exact HEAD, durable run ID and state, and the absolute worktree-local `docs/CURRENT_STATE.md` path
plus its SHA-256. The resolved CURRENT_STATE path must remain inside the resolved request worktree
(symlink/path escape fails closed). Agents must verify branch/HEAD and read that exact local file
before acting; missing or corrupt durable state, run-id mismatch, or missing identity inputs fail
closed without falling back to the root checkout copy. The identity block is context only and does
not mutate durable `.orchestration` state.

An escalated run may receive a strictly validated, append-only operator decision under its existing
`handoffs/` directory. The original phase contract is never rewritten. Additional allowed paths are
applied only as an in-memory effective-contract amendment for that run. Agent Orcha receives the
original contract, amendment, prior structured question, current worktree evidence, unresolved
verification failure, and remaining acceptance criteria; its exact correction prompt is persisted
under the run's `prompts/` directory with a SHA-256 identity before the saved implementer session is
resumed in the existing worktree.

### Post-result test-change authorization

When evidence validation escalates because a test file was deleted or had lines removed
(`suspicious_test_changes`), and `state.result_sha` is already set, the pre-result
`record-decision` verb cannot authorize that change (it requires the implementation worktree still
be at `base_sha` with no `result_sha`). Use the separate post-result verb instead:

- Operator supplies one JSON object: `run_id`, `decision` (`APPROVED`), non-empty `reason`, the
  exact `result_sha` this authorization is scoped to, exactly one repository-relative `file_path`,
  and `authorized_diff` — the exact unified diff for that file between `base_sha` and `result_sha`
  as the operator inspected it.
- At record time the controller independently recomputes `git diff base_sha..result_sha -- file_path`
  in the registered, clean implementation worktree (HEAD must equal `result_sha`) and accepts the
  decision only when that live diff matches `authorized_diff` exactly (line-ending normalized). The
  stored `authorized_diff` is never treated as ground truth on its own.
- Paths matching the phase contract's `prohibited_paths` are rejected. Pre-result escalations
  (no `result_sha`) are rejected with a message directing the operator to `record-decision`.
- On success an append-only `handoffs/post-result-decision-NN.json` is written and the run
  transitions to `VALIDATING_IMPLEMENTATION` (no orchestration-tooling repair / controller-HEAD
  gate).
- On every subsequent evidence-collection pass the controller re-globs those artifacts and, for each
  one, re-checks `decision.result_sha == state.result_sha` and re-computes the live file diff against
  `authorized_diff`. Only decisions that still pass both checks contribute their `file_path` to
  `authorized_test_changes`. Stale or mismatched decisions are omitted (not hard-errored) and the
  recomputed live diffs are recorded under `post_result_decision_rechecks` on the evidence artifact.
  All other evidence gates (ancestry, prohibited paths, governance docs, test command exit codes)
  remain fail-closed.

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
