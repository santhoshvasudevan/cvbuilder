# Current State

**Do not treat this file as self-certifying.** Verify Git and tests directly before relying on it.

This root-checkout `docs/CURRENT_STATE.md` describes the **root repository checkout** only.
Controller agent prompts also require durable `.orchestration` run state plus the
**worktree-local** `docs/CURRENT_STATE.md` for the request worktree; never substitute this
root file for an implementation/audit worktree copy.

## Repository State

- Product baseline: `cvbuild2` commit `437b179490b01697366ba70d75b6c72a6c83a7a4` (verified M2).
- Development-orchestration tooling line: `buildwithAgent`.
- **M3A: complete and merged.** The corrective run `m3a-c1-6df5432d618c` closed both deferred
  findings (`AUDIT-002`, `CLAUDE-M3A-002` — see `docs/DEFERRED_FINDINGS.md`, both `RESOLVED`) and
  was landed via merge commit `43fcee3` ("Operator landing of run m3a-c1-6df5432d618c after
  closure-format escalation"), an ancestor of the current `buildwithAgent` HEAD. `main` remains the
  incompatible V1 legacy line (V2-D038) and is unaffected.
- **M3B-S1 (`candidate_context` app, `CandidateContextSnapshot`, `direct_match` bucket): NOT yet
  landed.** See "M3B-S1 state" below — this is the most important thing for the next orchestrator
  to read before doing anything else.
- Last updated: 2026-09-24.

## M3B-S1 state

Three separate controller runs produced content-identical implementations of the same slice; none
reached `COMPLETED`, each for a different, narrow, non-content reason:

| Run ID | Result candidate | Where it stopped | Why |
|---|---|---|---|
| `m3b-s1-d323a242d9ff` | `1f63825` (tag `preserved/m3b-s1-d323a242d9ff-orphaned-implementation`) | Implementer finished and committed; controller never recorded the handoff | The supervising controller process was killed by the OS for low system memory before it could receive the implementer's structured response. The implementer session itself completed normally. |
| `m3b-s1-9aebc939eb4f` | `c701890` (tag `preserved/m3b-s1-9aebc939eb4f-verified-candidate`) | Reached `VALIDATING_IMPLEMENTATION` cleanly (both boundary carve-outs authorized, all 8 `required_tests` independently reverified 0→0 via `record-reverification-decision`) | Abandoned for a **self-inflicted, unrelated** cause: the operator merged and pushed a controller-tooling change (the `record-reverification-decision` verb itself) onto `buildwithAgent` while this run was still open, which moved the repository HEAD the run had pinned against. `resume`'s three tooling-repair helpers do not cover this failure class. See `docs/ORCHESTRATION_BACKLOG.md` item 4. |
| `m3b-s1-a6628234bbc3` | `541c4e7` (tag `preserved/m3b-s1-a6628234bbc3-audit-passed`) | **Reached `AUDITING` and the reviewer returned verdict `PASS`.** Failed only in the next stage, `CLOSURE_REVIEW`. | The `orcha_closure` adapter (routed to Codex/`gpt-5.6-terra`) rejected the closure-narrative request outright at the API level: `invalid_json_schema: 'additionalProperties' is required to be supplied and to be false`. `closure-narrative.schema.json` sets `additionalProperties: true` by design (this session's narrative-schema redesign, meant to tolerate extra fields); that provider's strict structured-output mode rejects any such schema before the model ever sees the request. **This is a tooling bug, not a candidate defect** — full diagnosis and two candidate fixes (neither built) in `docs/ORCHESTRATION_BACKLOG.md` item 10. |

**`541c4e7` is the furthest any candidate has gotten**: implementation, evidence collection, and an
independent reviewer audit (verdict `PASS`) all passed. The only remaining blocker before this
slice can land is the `closure-narrative.schema.json` / Codex strict-mode conflict above. **Fix
that first**, then either resume/re-plan against `541c4e7` (tag
`preserved/m3b-s1-a6628234bbc3-audit-passed`, branch `agent/m3b-s1-a6628234bbc3/implementation`) or
run a fresh implementation — the content is not the obstacle.

What `candidate_context` (as implemented across all three identical candidates) actually delivers:
a new Django app; `CandidateContextSnapshot` (immutable-after-creation, FK to a
`StageIdentifier.CANDIDATE_CONTEXT_BUILD` `StageRun` with null provider/model FKs); the
`direct_match` bucket populated via deterministic, bounded, lexically-scored retrieval over
`MemoryClaim` rows in `{CAREER_HISTORY, SKILL, ACHIEVEMENT}`, read-only against `candidate_memory`;
the other four context buckets present as schema placeholders only. The interim target-job signal
resolves from `JobApplication.employer`/`job_title` only (no `job_intake`/AJ dependency yet — a new
decision, expected ID `V2-D047`, is to be authored by the operator from the implementer's
`decisions_required` narrative once a candidate actually reaches `COMPLETED`; it has not been
authored yet because no run has completed).

M3B-S2 (and S3–S5) have not been formally planned or drafted as contracts. Do not draft S2 until
S1 has actually landed — there is nothing in this session's work that changes what S2 should
assume; the delivered content matches what the M3B-S1 contract specified throughout.

## Milestone-boundary inventory

`candidate_memory/tests/test_architecture.py`'s `FORBIDDEN_APPS` and
`job_applications/tests/test_settings.py`'s `NOT_YET_BUILT_APPS` both currently list the same six
apps (unchanged by this session, since M3B-S1 has not landed): `candidate_context`, `job_intake`,
`candidate_matching`, `positioning_strategy`, `resume_builder`, `reviews`. Per
`docs/IMPLEMENTATION_PLAN.md`'s milestone scope headings:

| App | Target milestone | Milestone scope |
|---|---|---|
| `candidate_context` | M3B (this slice) | `CandidateContextSnapshot`, five context buckets |
| `job_intake` | M4 | AJ recruiter analysis: URL/paste intake, Job Requirement Model, Recruiter Decision Model |
| `candidate_matching` | M5 | AC: deterministic context retrieval, `RequirementFit`, evidence strength, gaps/transferability |
| `positioning_strategy` | M5 | APS: candidate thesis, career narrative, positioning, title options (highest-judgment new component per V2-D027) |
| `resume_builder` | M6 | AB multi-pass: `ResumeContentPlan`, structured `ResumeDraft` |
| `reviews` | M5 (Gate 1) | `ReviewFeedback` referencing a `StageRun`, used by both Human Gate 1 (M5) and Gate 2 (M6) |

This mapping is inferred from `docs/IMPLEMENTATION_PLAN.md`'s scope sections by app-name
correlation, not from an explicit per-app decision record — verify against the plan before relying
on it for exact scoping. Whichever milestone lands next must remove exactly its own app's entry
from both files, following the same pattern (and the same `record-post-result-decision`
requirement, one call per file) used for `candidate_context` in M3B-S1.

## Requirement-ID hygiene note (FACT-003 / "requirements.md §6.1")

`.orchestration/contracts/M3A.json` and `M3A-C1.json` both list `"requirements.md §6.1"` as a raw
section-reference string inside their `requirement_ids` arrays, alongside proper requirement codes
like `FACT-002`/`FACT-003`. This is a documentation-hygiene inconsistency, not a content error:
`requirements.md` §6.1 ("Candidate Profile") corresponds to the properly-tracked requirement
`FACT-003` ("Hard integrity vs soft review warnings" — see
`docs/REQUIREMENT_TRACEABILITY.md`, which already cites `FACT-003` correctly). Both M3A and M3A-C1
are already landed and closed; this note is informational only — it does not reopen either run —
but any future contract's `requirement_ids` array should use requirement codes exclusively, never a
raw section pointer.

## Verified Working

- M1/M2 product baseline remains the accepted product line; confirm with `make verify` on the
  checkout under test.
- External controller: strict YAML config; Codex Orcha, Cursor Implementer, enabled Claude Reviewer
  (see "Reviewer routing" in `docs/ORCHESTRATION_HANDOVER.md` for the current live routing),
  disabled Codex reviewer rollback, and fake adapters; atomic state; sanitized events/logs;
  structured phase/implementer/audit/closure schemas; Git evidence; isolated worktrees; bounded
  questions/corrections; safe resume/abort; three-pane observational tmux; per-run
  `supplemental-audits/` sibling for non-pipeline audit evidence; worktree-local startup identity on
  every agent request; `record-decision` / `record-post-result-decision` /
  `record-reverification-decision` operator verbs (see `docs/DEVELOPMENT_ORCHESTRATION.md`).
- `doctor` qualifies installed tooling; Cursor authentication and live model availability remain
  operator prerequisites and can change over time — re-run `doctor` rather than trusting historical
  login notes.
- `candidate_memory` Django app with provenance-bearing source ingestion, MemoryClaims/supports,
  unresolved MemoryConflicts, CandidateProfile preferences, CareerEngagement roster,
  StaticResumeProfile, and sequenced ExperienceSlot collection (copy-on-create + source FK).
  Immutable claim/source provenance (`AUDIT-002`) and generic date-contradiction detection
  (`CLAUDE-M3A-002`) both `RESOLVED` — see `docs/DEFERRED_FINDINGS.md`.
- `candidate_context` app content (implemented three times, identically; not yet landed) — see
  "M3B-S1 state" above.

## Known Limits / Operator Actions

- Non-date contradiction categories beyond the resolved date-contradiction defect remain deferred.
- Certifications/languages are stored only on StaticResumeProfile as static JSON; no LLM path
  generates them in M3A.
- Adversarial audit-test commits still require a separate operator-approved write/transfer action.
- Independently commissioned supplemental audits belong under `supplemental-audits/` and do not
  drive the state machine until translated through an authorized schema-valid controller path.
- `docs/DECISIONS.md` and `requirements.md` are operator-authored governance artifacts, never
  implementer scope, enforced by the controller's governance gate regardless of contract
  `allowed_paths` — see `docs/DEVELOPMENT_ORCHESTRATION.md`.
- Operator alone decides whether to merge any accepted candidate into `buildwithAgent`.

## Next Recommended Action

1. Fix the `closure-narrative.schema.json` / Codex strict-structured-output conflict
   (`docs/ORCHESTRATION_BACKLOG.md` item 10) — this is the single remaining blocker keeping
   M3B-S1 from landing.
2. Then resume or re-plan against the audit-passed candidate `541c4e7` (tag
   `preserved/m3b-s1-a6628234bbc3-audit-passed`) rather than re-deriving the implementation again.
3. Only after M3B-S1 actually reaches `COMPLETED` and is merged: author decision `V2-D047` in
   `docs/DECISIONS.md` from the landed candidate's `decisions_required` narrative, then plan M3B-S2.
