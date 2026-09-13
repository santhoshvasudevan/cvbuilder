# Development Orchestrator Runbook

Run every command from the repository root. No command below makes a paid/live model call until the
explicit `run` step, and `run` is refused until a clean contract has been approved.

## Qualification and planning

```text
.venv/bin/python -m tools.dev_orchestrator validate-config
.venv/bin/python -m tools.dev_orchestrator doctor
.venv/bin/python -m tools.dev_orchestrator plan --phase M3A
```

`doctor` checks Git boundary/ancestry, Git/tmux/jq, installed Codex machine-output flags and login,
Cursor binary/login, configured model syntax, runtime/worktree safety, redaction, and disabled optional
adapters. It never calls a model. `WARN Cursor authentication: Not logged in` must be resolved with the
operator's normal Cursor login flow before the first live run.

The planning command prints the run ID and generated contract path. Inspect both representations:

```text
jq . .orchestration/runs/<run-id>/phase-contract.json
sed -n '1,260p' .orchestration/runs/<run-id>/phase-contract.md
```

Do not approve if `future_implementation_base_sha` is not the accepted orchestration bootstrap commit
or if any scope item is ambiguous. V2-D031 explicitly leaves ExperienceSlot copy-versus-reference as
an M3A implementation detail; the contract permits the smallest approach only if it preserves source
linkage, explicit operator selection, and static metadata ownership.

## Approval and supervised launch

```text
.venv/bin/python -m tools.dev_orchestrator approve --run-id <run-id>
.venv/bin/python -m tools.dev_orchestrator run --phase M3A --run-id <run-id>
```

`approve` refuses a dirty tree or changed HEAD. The live `run` creates implementation and audit
worktrees under `.orchestration/worktrees/<run-id>/` and invokes the configured agents. It never merges
or pushes. Cursor must be authenticated before this step.

Attach the observational dashboard from another terminal:

```text
.venv/bin/python -m tools.dev_orchestrator attach --run-id <run-id>
```

## Operation and recovery

```text
.venv/bin/python -m tools.dev_orchestrator status --run-id <run-id>
.venv/bin/python -m tools.dev_orchestrator resume --run-id <run-id>
.venv/bin/python -m tools.dev_orchestrator abort --run-id <run-id>
```

`resume` reloads atomic state and reuses a captured session ID where supported. `abort` marks the run
aborted but deliberately leaves branches/worktrees intact for inspection and recovery. An
`OPERATOR_ESCALATION` or audit-test commit is not auto-transferred; inspect state/events/handoffs and
make a separately authorized decision.

For an operator-approved bounded amendment to an existing `OPERATOR_ESCALATION` run, write the exact
decision object to a temporary JSON file outside the repository, then record it and resume:

```text
.venv/bin/python -m tools.dev_orchestrator record-decision --run-id <run-id> --file /tmp/operator-decision.json
.venv/bin/python -m tools.dev_orchestrator resume --run-id <run-id>
```

`record-decision` strictly validates the object, preserves the original phase contract byte-for-byte,
recovers only a complete schema-valid prior handoff from durable Cursor output, and creates an
append-only decision artifact. On `resume`, Agent Orcha generates the correction prompt, the controller
persists its exact text and SHA-256 identity, and the existing Cursor session/worktree are reused. The
command refuses missing/unregistered worktrees, mismatched run IDs, prohibited paths, unsafe relative
paths, and any main-checkout change beyond a clean descendant commit limited to the orchestration
repair implementation and these two orchestration documents.

When the implementer evidence has already passed but the reviewer process itself fails before writing
an audit handoff, repair and commit only the orchestration tooling, then run the same `resume` command.
The controller will retry the audit only when both existing worktrees are registered, clean, and at the
recorded result SHA, the evidence artifact exists, and the latest durable event is a reviewer failure.
It does not rerun Cursor or create another implementation worktree.

When the implementer has already committed a correction but escalated because the Cursor final message
was not strict schema-only JSON, repair and commit only the orchestration tooling, then `resume`. The
controller retries the saved Cursor session only when the implementation worktree is registered, clean,
and at a HEAD that descends from the approved base and differs from the prior recorded result, the
session ID exists, the latest event is an implementer structured-output failure, and no conflicting
handoff exists for the cycle. The persisted prompt forbids further file changes and demands only the
implementer schema JSON for that HEAD; evidence collection then reruns reported commands against the
full base-to-result boundary. Strict-output prompts include only executable required tests in
`test_commands` instructions.

When evidence validation rejects that handoff only because `test_commands` contained narrative
`required_tests` text, repair and commit orchestration tooling, then `resume` again. The controller
keeps the rejected handoff intact, asks Cursor for a corrected schema-only handoff pinned to the same
`result_sha`, stores the replacement under an append-only corrected artifact, and points validation at
it. Duplicate resume reuses that artifact only when it remains schema-valid and semantically identical
to the current run (`status=IMPLEMENTED`, matching `base_sha`/`result_sha`); forged or mismatched
corrected artifacts fail closed without re-prompting Cursor or consuming another correction cycle.

An audit that returns `CORRECTION_REQUIRED` may include a failed reviewer-side command when the
isolated audit worktree cannot access local credentials. The controller records that failure and the
findings, independently reruns every successful reported command, and continues to Agent Orcha. A
failed command with `PASS` remains an immediate escalation. Successful reviewer `git diff --check`
commands may use zero endpoints, one or two verified revision endpoints, or exactly one verified
`A..B` / `A...B` range token. The controller rejects pathspecs, nonexistent revisions, nested or
multiple range forms, shell operators, option injection, and write-capable git forms.

## Non-live dry run and tests

```text
.venv/bin/python -m tools.dev_orchestrator run --phase M3A --dry-run
.venv/bin/python -m unittest discover -s tools/dev_orchestrator/tests -v
make verify
make secrets
```

Dry-run planning validates and materializes a contract, records `live_agents_invoked: false`, and stops
at `AWAITING_PHASE_APPROVAL`. Its manifest is permanently non-approvable; create a normal `plan` for a
live run. Automated tests use fake adapters only.
