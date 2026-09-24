# Orchestration Backlog

Proposals only. Nothing in this file has been built. Each item names the concrete cost it has
already imposed, so a future orchestrator can rank them by evidence rather than guesswork.

## 1. `phase-contract.schema.json` validates shape, not content (cost: 4 live-run defects)

`validate_phase_contract` (`tools/dev_orchestrator/schemas.py`) checks that most contract fields
are lists of strings and nothing more. It cannot catch: a `required_tests` entry that will never
pass `_validate_verification_argv`; a `required_documentation_updates` entry that is narrative
prose instead of a bare path; `docs/DECISIONS.md`/`requirements.md` appearing in `allowed_paths`;
an `acceptance_criteria` entry with no executable or evidence-collector backing at all. All four
reached a *live run* before being caught, each costing a diagnose-amend-reverify-replan cycle on
the M3B-S1 contract. A tightened schema encoding the checklist in
`docs/DEVELOPMENT_ORCHESTRATION.md` — shape *and* semantic constraints — would have caught all
four at `plan` time, before any agent ran.

## 2. `_validate_verification_argv` runs at evidence collection, not plan time (same root cause as #1)

The allowlist that decides whether a `required_tests` entry is executable is only ever consulted
once evidence collection actually tries to run the reported commands, deep into a live run. A
plan-time validator that walks every `required_tests` entry through the same allowlist function
before `approve` would surface an unrunnable entry immediately, with zero agent cost.

## 3. `authorized_governance_changes` is never set anywhere in the codebase

`EvidenceCollector.collect_and_validate` accepts an `authorized_governance_changes` parameter that
would suppress the unconditional governance-file veto (`docs/DECISIONS.md`/`requirements.md`) — but
no caller in `controller.py` ever passes `True`. This means the flag is dead code: governance files
are unconditionally hard-blocked for every controller-run implementer, by design, though this was
never documented as intentional until this session. Either remove the unused parameter (if the
governance veto should never be liftable through this path) or document explicitly that it is
reserved for a future operator-only override and is not currently reachable.

## 4. `OPERATOR_ESCALATION` is terminal; `resume` short-circuits before any repair helper can apply

`OPERATOR_ESCALATION` is a member of `TERMINAL_STATES` (`state.py`). `resume`'s three tooling-repair
helpers (`_retry_reviewer_after_tooling_repair`, `_retry_implementer_strict_output_after_tooling_repair`,
`_retry_implementer_evidence_handoff_after_tooling_repair`) are each gated on the run's *exact last
event* matching one narrow predicate; if none matches, none mutates state, and the very next check
(`if RunStateName(state.state) in TERMINAL_STATES: return state`) returns the run unchanged. This
has now forced **six** re-plans in this session alone:

1. `job_applications` boundary-file coverage gap (contract defect).
2. `required_tests` shape defect (inline `python -c`).
3. `required_documentation_updates` shape defect + governance-gate defect.
4. The controller process was OOM-killed mid-run; no recorded `result_sha`, no handoff — none of
   the three helpers apply because `result_sha` was never set.
5. The test-database container cleanly shut down under memory pressure; the evidence collector's
   independent re-run observed a different exit code than the implementer's claim — this class is
   now covered by the new `record-reverification-decision` verb, but only *after* a sixth escalation
   forced building it.
6. A tooling-HEAD-pin mismatch from merging that very verb while the run was still open — no
   existing or new verb covers it; the run had to be abandoned.

**Three narrow single-purpose verbs have now accumulated** (`record-decision`,
`record-post-result-decision`, `record-reverification-decision`) specifically because
`OPERATOR_ESCALATION` conflates every failure class into one terminal bucket with no general notion
of "recoverable." Each new recoverable-failure class discovered in production has required building
another bespoke verb rather than fixing the underlying classification gap.

**Proposed redesign** (not built): split `OPERATOR_ESCALATION` into at least two states, or add a
`recoverable: bool` / `recovery_class: str` field to `RunState` set at escalation time by whichever
code path raises the failure. A single generic `resume` dispatch would then route by
`recovery_class` to the correct narrow handler (existing or new), rather than each handler
independently re-deriving "does my specific failure signature match the last event" from scratch.
This would also make it possible to _statically enumerate_ which failure classes are recoverable
today, instead of discovering the boundary empirically one production run at a time, as this
session did six times.

## 5. The tooling-HEAD-pin check has no operator-acknowledgement path (cost: 1 abandoned run)

`_verify_run_controller_head` compares the live repository HEAD against `state.controller_sha or
state.base_sha` on every `execute()` call, with no way for the operator to say "yes, I know the
repo moved, it's fine, continue." The three existing `resume` helpers each bump `controller_sha`
only as a side effect of a *different*, narrower repair; none is a general acknowledgement
mechanism. A fourth, explicitly-scoped verb (`record_controller_advance_decision`, say) could let
the operator name the reason a tooling/doc commit landed mid-run and bump `controller_sha`
directly — but see item 4: it may be better to fix the underlying escalation-classification gap
once than to keep adding narrow verbs for each newly-discovered recoverable class.

## 6. The suspicious-test-change flag could route to the reviewer instead of escalating

Today, any test-file deletion or line removal (`suspicious_test_changes`) unconditionally escalates
to the operator, who must run `record-post-result-decision` once per affected file before the run
can continue. For the one narrow, recurring, and legitimate case this session hit four times — a
milestone-boundary carve-out removing exactly one app's name from `FORBIDDEN_APPS`/
`NOT_YET_BUILT_APPS` — the operator's own review is mechanical and repetitive: confirm `0` added /
`1` deleted, confirm the single line is the target app's own entry, confirm every other entry
survives. A contract could instead declare this specific carve-out as a **mandatory reviewer audit
item** (an acceptance criterion the reviewer must explicitly check and report on) rather than an
operator escalation, freeing the operator gate for genuinely unanticipated suspicious changes.

## 7. Accepted residual: same-process concurrent-CLI race on the single-use marker

Codex's review of `record-reverification-decision` (`3354b22`) found: the single-use marker scan
(`paths.handoffs.glob("reverification-decision-*.json")`) happens before the (potentially long)
test re-execution, with no per-run lock or compare-and-swap. Two concurrent CLI invocations against
the same run could both pass the scan before either writes its marker, and — because
`_next_artifact_path` only guarantees a unique *filename*, not a unique *decision* — both could
write distinct markers and both transition state. This is accepted as a residual risk given the
existing "confirm `pgrep -fl dev_orchestrator` shows nothing running before launching" operator
discipline (this session's own standing rule), not because the race is theoretically impossible.
A per-run advisory lock file, held for the duration of any state-mutating verb, would close it
completely if a future orchestrator judges the residual unacceptable.

## 8. Whether the Phase 1 verification lock covers the controller-invoked implementer's own test runs

A verification lock (`tools/test_database_lock.py`, invoked by `make verify`) was added earlier
this session specifically so concurrent `make verify` runs could not collide on the shared
`test_cvbuilder` Postgres database. Despite that, `implementer-002.stdout.log` for run
`m3b-s1-d323a242d9ff` shows Postgres itself reporting `database "test_cvbuilder" is being accessed
by other users` mid-run, during the OOM incident. This session did not determine conclusively
whether that specific contention came from two controller-launched processes for the *same* run
(most likely, given a mistaken manual `&` background launch collided with a harness-tracked
relaunch — see the Operational preflight section of `docs/DEVELOPMENT_ORCHESTRATION.md`) or from
the lock genuinely not covering the code path the controller-invoked implementer uses to run its
own tests (e.g. if the implementer's Cursor session shells out to `manage.py test` directly rather
than through `make verify`/`make test`, it may never acquire `test_database_lock.py`'s lock at
all). This needs a direct check: does `candidate_context`'s implementer session's own test
invocations go through a Makefile target that acquires the lock, or bypass it entirely by calling
`manage.py test` directly? If the latter, the lock covers operator-invoked verification but not
agent-invoked verification, and contention under concurrent agent activity remains possible even
with the lock in place.

## 9. AC-8-style checks scan only `.py` files and prove constants match without proving use

`candidate_context.tests.test_local_postings_exclusion` (this session's own hardening of an
earlier weak string-grep test) proves two things: no `.py` file under `candidate_context/**`
contains the literal string `"samplejd"`, and specific named constants
(`ACME_EMPLOYER`/`ACME_JOB_TITLE`/`GLOBEX_EMPLOYER`/`GLOBEX_JOB_TITLE`) in
`test_direct_match_retrieval.py` match freshly-parsed values from the committed fixture files. It
does **not** prove that `test_direct_match_retrieval.py` actually *uses* those constants in its
JobApplication fixtures (a test could define matching constants and then simply never reference
them, or reference different literal strings instead), and it does not scan non-`.py` files (a
`samplejd` reference embedded in a fixture, migration data file, or docstring-adjacent text file
would not be caught). A stronger version would additionally assert the constants are referenced
inside the `JobApplication(...)` construction calls in that same test module, and widen the file
scan beyond `*.py`.

## 10. Closure-narrative schema rejected by the `orcha_closure` adapter's structured-output API

Discovered live, this session's final Part A attempt (run `m3b-s1-a6628234bbc3`): the reviewer's
audit passed (verdict `PASS`, candidate `541c4e7`), but `CLOSURE_REVIEW` failed immediately with an
API-level rejection, not a model or content error:

```
invalid_request_error / invalid_json_schema: In context=(), 'additionalProperties' is required
to be supplied and to be false. (param: text.format.schema)
```

`tools/dev_orchestrator/schemas/closure-narrative.schema.json` (and
`implementer-narrative.schema.json`, which shares the same pattern) sets
`"additionalProperties": true` at the schema root — a deliberate design choice from this session's
own narrative-schema redesign, meant to tolerate extra agent-supplied fields without hard-failing.
`agents.orcha_closure` in `.orchestration/config.yaml` is routed to the `codex` adapter
(`model: gpt-5.6-terra`); that provider's structured-output ("Structured Outputs") mode requires
`additionalProperties: false` at every level of a strict-mode schema, and rejects the request
outright — before the model ever sees it — when that constraint is violated. The narrative-schema
design and this specific provider's strict-mode requirement are in direct conflict. This candidate
(`541c4e7`, tag `preserved/m3b-s1-a6628234bbc3-audit-passed`) reached `CLOSURE_REVIEW` for the first
time since that redesign landed, which is why this was not caught earlier: the code path was never
previously exercised end-to-end with a `codex`-routed closure agent.

Two fixes are plausible, neither built: (a) set `additionalProperties: false` at every object level
in `closure-narrative.schema.json` (and `implementer-narrative.schema.json`, before it hits the same
wall on some future run) and accept that extra fields will now hard-fail instead of being tolerated
— trading the narrative-schema's original tolerance goal for compatibility with strict-mode
providers; or (b) keep `additionalProperties: true` for genuinely tolerant validation, but detect
when the routed adapter requires strict mode and pass a stripped/level-appropriate schema variant to
that specific provider only. This is the most urgent item in this backlog: it blocked M3B-S1 from
reaching `COMPLETED` after every other stage — implementation, evidence, and audit — had already
passed.
