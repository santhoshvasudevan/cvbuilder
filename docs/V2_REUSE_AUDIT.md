# CVBuilder V2 — `main` Branch Reuse Audit

**Status:** Completed (M0.2)
**Date:** 2026-09-08
**Type:** Audit / history record — **not** a canonical architecture source. Where this document and `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, or `requirements.md` appear to disagree, those documents govern; this file records what was found on `main` and what was recommended at the time of the audit.

## 1. Repository State At Time Of Audit

- Branch under development: `cvbuild2`, HEAD `04b52b9` ("chore: remove tracked macOS metadata").
- `main` HEAD: `d5bdcea` ("chore: update gitignore and development commands").
- Merge-base of `cvbuild2` and `main`: `fd02af8` ("docs: M0.1 Candidate Memory bootstrap design (D-015) and lifecycle audit fixes") — confirmed to match requirements.md's stated baseline commit.
- `main` is 47 commits ahead of the merge-base, implementing V1 milestones M1–M7: Django/Postgres foundation (`58d05ee`), LLM provider control plane (`8627a93`), Candidate Memory (`8d7fa7c` + hardening commits through `5b23255`), Job Intake/AJ (`20c83ba`), Candidate Matching/AC + Gate 1 (`aed6b32`), Resume Builder/AB + Gate 2 (`88c8770`), retrieval/gate hardening (`86f5583`, `1418c15`, `983afbd`, `66012aa`), and a staged-workflow refactor (`0594eb2`, D-041 — the newest feature commit on `main`).
- No merge or cherry-pick from `main` has occurred. This audit is read-only.

## 2. Reuse Matrix

### 2.1 REUSE_AS_IS

| Area | File(s) | Commit(s) |
|---|---|---|
| Docker/Postgres | `docker-compose.yml` | `58d05ee` |
| Env template | `.env.example` | `58d05ee` |
| Ignore rules | `.gitignore` | `58d05ee` |
| Local dev commands | `Makefile` | `58d05ee` |
| Dependency pins | `pyproject.toml`, `requirements.txt`, `requirements-dev.txt` | `58d05ee` |
| Shared UI shell | `templates/base.html` | `58d05ee` |
| LLM adapters | `llm_provider/adapters/base.py`, `fake.py`, `openai.py`, `gemini.py`, `nvidia.py`, `openrouter.py` | `8627a93` + provider-specific hardening (`configure_gpt54_defaults`, `openrouter_free_router`) |
| Schema translation | `llm_provider/schema_translation.py` | `8627a93` |
| Retry/error handling | `llm_provider/retry.py`, `llm_provider/errors.py` | `8627a93` |
| Eligibility/model selection/console | `llm_provider/services/eligibility.py`, `model_selection.py`, `console.py` | `8627a93` |
| Provider registry models | `llm_provider/models.py` — `LLMProvider`, `LLMModel`, `LLMCallLog`, `OpenRouterKeyStatus` | `8627a93` + migrations `0002`–`0010` |
| Network-guarded test runner | `llm_provider/testing.py` | `8627a93` |
| Job URL fetch | `job_intake/services/fetch.py` | `20c83ba` |
| Job requirement integrity validator | `job_intake/validators/integrity.py` | `86f5583` (D-022, corrected same-day by D-023) |
| Candidate memory ingestion pipeline | `candidate_memory/services/bootstrap.py`, `chunking.py`, `extraction.py`, `classification.py`, `storage.py`, `quote_recovery.py`, `subject_scope.py`, `comparable_values.py`, `conflicts.py`, `confirmation.py`, `lifecycle.py`, `revision.py`, models `CandidateMemory`/`MemorySourceDocument`/`MemoryClaim`/`MemoryClaimSupport`/`MemoryConflict`, management commands | `8d7fa7c` + hardening commits through `5b23255` |
| Deterministic requirement pre-resolution | `candidate_matching/services/static_requirements.py` | D-019 |
| Claim dedup | `candidate_matching/services/dedup.py` | `20c83ba` |
| `resume_builder` delivery views | `resume_builder/views.py`, `urls.py`, `admin.py`, `preview.html` | `88c8770` |
| Gate rerun-requires-comment invariant | `reviews/services.py` | `88c8770`/`1418c15` |

### 2.2 REUSE_WITH_ADAPTATION

| Area | File(s) | Commit(s) | Adaptation required |
|---|---|---|---|
| Django settings/urls | `config/settings.py`, `config/urls.py`, `manage.py` | `58d05ee` | Update `INSTALLED_APPS`/url includes for V2 app boundaries (`candidate_context`, `positioning_strategy` added) |
| Stage enum | `llm_provider/models.py` — `StageModelAssignment.Stage` | `8627a93` | Rewrite enum values to the canonical V2 stage vocabulary (see `docs/ARCHITECTURE.md` §12); one migration. No other file in `llm_provider` hardcodes stage names. |
| Reasoning-level granularity | `llm_provider/models.py` — `LLMModel` | `8627a93` | Add `supported_reasoning_levels` (currently a boolean `supports_reasoning` only). *Resolved in the M0.2 follow-up: `supported_reasoning_levels` is the canonical source of truth; `supports_reasoning` becomes a derived helper only — see V2-D034.* |
| `job_applications` aggregate | `job_applications/models.py`, `services.py` | `58d05ee`, `20c83ba`, `aed6b32`, `88c8770`, later UX commits | Superseded by the `JobApplication` + `StageRun` + `JobApplicationStageState` redesign closed in M0.2 (see `docs/ARCHITECTURE.md` §5). V1's freshness (`based_on_X_id`), locking, and dashboard-computation *patterns* remain the reference design; the growing `current_*` FK list does not. |
| Job requirement model | `job_intake/models.py`, `schemas.py` (`ExtractedRequirement`), `services/analyze.py`, `services/intake.py` | `20c83ba`, `86f5583` | Category taxonomy and stable `JR-NNN` ID assignment are reusable; add `RecruiterDecisionModel` as a new sibling artifact (no V1 precedent). *Resolved in the M0.2 follow-up: V1's single flat category is replaced by three orthogonal dimensions (`requirement_priority`, `requirement_domain`, `origin`) — see V2-D033.* |
| Candidate static-profile boundary | `candidate_memory/services/static_profile_boundary.py`, `CareerEngagement` model, `services/career_engagement.py` | D-019 | Already enforces "LLM cannot generate static fields" via `extra="forbid"` contracts; needs a thin `ExperienceSlot` selection/sequencing layer over the existing (unordered, N-sized) `CareerEngagement` roster — see `docs/ARCHITECTURE.md` §6 |
| Candidate rules | `candidate_memory/models.py` — `CandidateRule` | `8d7fa7c` | Map rule_type taxonomy onto V2's `operator_corrections[]`/`wording_preferences`/`known_gaps` fields |
| Claim-engagement mapping | `candidate_memory/services/engagement_mapping.py`, `ClaimEngagementMapping` | `bec9edd`, `ce12a80`, `7d4165f` | Reusable deterministic (non-fuzzy) linking; needs to interoperate with the new five-bucket context builder |
| Retrieval mechanics | `candidate_matching/services/retrieve.py`, `bounded_retrieval.py`, `retrieval_limits.py`, `normalization_limits.py` | `20c83ba`, hardened `86f5583`/`66012aa` | Eligibility/manifest/fail-closed-budget *mechanics* are reusable building blocks; the per-requirement-only BM25 *selection strategy* must be replaced by a five-bucket selector |
| Disposition coverage validator | `candidate_matching/validators/disposition_coverage.py` | D-014 | Widen `VALID_DISPOSITIONS` from 4 values to V2's 6-value set (add `STRONG_MATCH`, `TRANSFERABLE`); otherwise this is the single closest-to-V2-philosophy file in the app (ID-existence check only, per-item downgrade rather than whole-build rejection) |
| `FitAssessment`/`RequirementAssessment` persistence pattern | `candidate_matching/models.py` | `20c83ba`, `0594eb2` | Append-only/versioned + frozen-identity-FK + manifest-inspectability pattern is sound; field set must expand to match `RequirementFit` |
| Markdown renderer | `resume_builder/rendering/markdown.py` | `88c8770`, D-019 | Already resolves company/date/title/location exclusively from static data, never from model output — swap the unbounded `CareerEngagement` roster query for a fixed `ExperienceSlot` collection query |
| AB single-call state machine | `resume_builder/services/staged_build.py` | `88c8770`, `0594eb2` | Prepare→execute→edit→approve shape is sound and stage-agnostic; must run once per AB sub-stage (Plan/Draft/Critique/Refine) instead of once total |
| AB prompt-construction craft | `resume_builder/services/generate.py` | `88c8770` | Static-fact-invention prohibition, evidence-citation discipline, and pre-call token-budget check are reusable; needs sibling request-builders for the 3 new AB sub-stages |
| Fabrication/completeness check *logic* | `resume_builder/validators/no_fabrication.py`, `completeness.py` | `017ea0a`, `1418c15` | Claim-ID-existence and engagement-correctness checks are useful signals; **consequence must be inverted** from `raise`+discard-entire-draft to warning collection — see hard/soft split in §7 below and `docs/ARCHITECTURE.md` §11 |
| `ReviewFeedback` | `reviews/models.py` | `88c8770` | Extend `Target` enum to add `APS` (and optionally per-AB-substage granularity). *Resolved in the M0.2 follow-up: V2 drops the `Target` enum entirely in favor of a nullable `stage_run` FK plus `gate` — see V2-D032.* |
| Gate stage-card/selector helpers | `reviews/views.py`, `services.py` | `88c8770`, `1418c15` | Already stage-parameterized; extend `GATE1_MODEL_STAGES`/`GATE2_MODEL_STAGES` tuples for the new stage vocabulary |
| Single-call review template | `reviews/views_m6.py`, `m6_review.html`, selector partials | `88c8770` | Best available template for each new AB sub-stage page; clone per stage |
| Gate 1/Gate 2 templates | `reviews/templates/reviews/gate1.html`, `gate2.html` | `aed6b32`, `88c8770` | Need substantial new sections (CandidateContext, APS, deterministic warnings, critique, quality score) — not a port, an extension |

### 2.3 DO_NOT_REUSE

| Area | File(s) | Commit(s) | Why |
|---|---|---|---|
| AC 3-stage orchestration | `candidate_matching/services/staged_run.py` | `0594eb2` (D-041) | Implements `AC_NORMALIZE → AC_RANK → AC_MATCH` as three independently-authorized calls — precisely the chain V2-D005/CLAUDE.md say not to recreate without fresh evidence. (The single-call lock-version/revision/invalidation *pattern* underneath is separately worth generalizing into `StageRun`, per §2.2/§7.) |
| AC normalize/rank calls | `candidate_matching/services/normalize.py`, `rank.py` | D-015/D-020 | Justified historically by one narrow, measured BM25 recall bug (foreign-language/paraphrased requirement vocabulary), not a positioning need. Do not carry forward by default. |
| AC match call | `candidate_matching/services/assess.py` | `20c83ba`, `86f5583` | Requirement-anchored only; no capability for differentiator identification or career-narrative assessment (confirmed: no prompt instruction or schema field for AC-006/AC-007 exists anywhere in `main`) |
| AC requirement-fit schema | `candidate_matching/schemas.py` — `RequirementAssessmentItem` | `20c83ba` | Only ~3 of V2's 10 `RequirementFit` fields have any analogue; no `STRONG_MATCH`/`TRANSFERABLE` disposition, no `evidence_strength`, no `experience_level`, no `risk`/positioning fields |
| AB context/build services | `resume_builder/services/context.py`, `build.py` | `88c8770` | Built entirely around V1's `baseline_chronology_manifest` pinned-evidence mechanism, superseded architecturally by `CandidateContextSnapshot` |
| ResumeDraft schema/model | `resume_builder/models.py` (`ResumeDraft`, `ResumeElement`), `schemas.py` (`AgentBuilderOutput`) | `88c8770` | Broader than V2's minimal 6-field contract (includes certifications/languages, unbounded engagement-keyed sections, no fixed 3-slot cardinality) |
| Legacy AC review UI | `reviews/views_m5.py`, `m5_stage.html` | `aed6b32` | Hardcoded to the discarded 3-stage AC slug routing (`normalize`/`rank`/`match`) |
| Fabrication/completeness hard-reject behavior | `resume_builder/validators/no_fabrication.py`, `completeness.py` (as currently invoked) | `017ea0a`, `1418c15` | Whole-build rejection on any single uncited claim directly violates FACT-003; see hard/soft split below |

### 2.4 Confirmed Absent On `main` (net-new V2 work, not a reuse question)

- `RecruiterDecisionModel` — zero fields/prompt instructions in `job_intake` (confirmed by schema and prompt inspection).
- Five-bucket `CandidateContextSnapshot` — zero grep hits for differentiator/career-narrative/direct-match/foundation concepts anywhere in `candidate_memory` or `candidate_matching`.
- `positioning_strategy` app / APS artifact — `git ls-tree -r --name-only main | grep -i positioning` returns nothing.
- `ResumeContentPlan`, recruiter critique call, refinement call, deterministic AB warnings pass — none exist; V1's Agent Builder is a single call.

## 3. Recommended Reuse Order

1. Foundation/infra copy (`docker-compose.yml`, `.env.example`, `.gitignore`, `Makefile`, `pyproject.toml`, `requirements*.txt`, `templates/base.html`).
2. `llm_provider` cherry-pick, file-by-file, plus the `StageModelAssignment.Stage` enum rewrite.
3. `job_applications` — implement the closed `JobApplication`/`StageRun`/`JobApplicationStageState` design (see `docs/ARCHITECTURE.md` §5, closed in M0.2), informed by but not copied from V1's `services.py` freshness/locking/dashboard patterns.
4. `candidate_memory` ingestion pipeline + `static_profile_boundary.py` + `CareerEngagement`, wrapped with an `ExperienceSlot` selection/sequencing layer.
5. Build `candidate_context` (five-bucket snapshot) — net new, on top of #4 and `candidate_matching`'s dedup/eligibility/manifest mechanics.
6. `job_intake` cherry-pick + add `RecruiterDecisionModel` (prompt/schema work only; orchestration unchanged).
7. Rebuild AC as one `AC_ASSESS` call: reuse `static_requirements.py`/`dedup.py`/`disposition_coverage.py` (widened enum); discard `staged_run.py`/`normalize.py`/`rank.py`; new `RequirementFit` schema.
8. Build APS (`positioning_strategy` app) — no reuse available.
9. Rebuild AB as 4-pass: reuse `staged_build.py`'s per-call pattern (run 4x), `rendering/markdown.py`, `generate.py` prompt craft; invert `no_fabrication`/`completeness` to the hard/soft split (§7); new schemas.
10. Extend `reviews` Gate 1/Gate 2 with new sections; clone `m6_review.html` per AB sub-stage.

## 4. Risks

- **Aggregate-root under-scoping risk:** the `JobApplication`/`StageRun`/`JobApplicationStageState` design must be finalized correctly before other apps reference it, since every domain artifact now references its own `StageRun`.
- **Enum-drift risk:** `StageModelAssignment.Stage`, AC disposition set, and (separately, per §7 below) `FitExperienceLevel` all need coordinated but independent redesign.
- **Cardinality-mismatch risk:** `CareerEngagement` (N, unordered) → `ExperienceSlot` (exactly 3 active primary slots, sequenced) needs a real selection/ordering mechanism, not just a data migration.
- **Behavior-inversion risk:** softening `no_fabrication.py`/`completeness.py` from exceptions to warnings must preserve the checks that stay hard (fabricated claim IDs, static-metadata corruption, schema violations) per the hard/soft split — a blanket "make it a warning" change would be wrong.
- **Scope-underestimation risk:** the five-bucket context builder and APS are both 100%-new, high-judgment components with no reference implementation anywhere in `main` — treat as the highest-uncertainty items in the plan.
- **Data risk (resolved during M0.2):** `docs/AC/AC-MEMORY_PROFILE.md`, `AC-profile_english.md`, `AC-profile_german.md`, and `docs/CANDIDATE_MEMORY_SNAPSHOT.md` were found deleted (unstaged) in the working tree at the start of the M0.2 session. These are real candidate source documents (named explicitly in `bootstrap_candidate_memory`'s docstring), not V2-planning cruft. They were confirmed present and intact as of M0.2 closure.

## 5. Planning Gaps Identified (closed in M0.2 — see `docs/DECISIONS.md`)

1. `requirements.md` §4 previously listed `Applied`/`Interviewing`/`Rejected` inside the same operator-facing-states list as `New`/`Analysis`/`Positioning`/`Preparation`/`Ready`, while also stating pipeline state and application outcome "remain separate" — self-contradictory. Closed by V2-D021.
2. `StageRun` ownership was ambiguous between `llm_provider` and per-domain-app placement. Closed by V2-D022 (`StageRun` belongs to `job_applications`/workflow layer).
3. CLAUDE.md/requirements.md's description of a single "old four-call AC chain" was imprecise — V1's chain was 3 calls in `candidate_matching` (`AC_NORMALIZE`/`AC_RANK`/`AC_MATCH`) plus 1 separate call in `resume_builder` (`AB_BUILD`), not 4 calls inside AC. Corrected throughout the canonical docs.
4. V2's AC-005 experience-level enum and V1's `MemoryClaim.experience_level` enum used different value sets with no defined mapping. Closed by V2-D023 (`FitExperienceLevel` is a distinct AC-layer concept; no forced migration of `MemoryClaim`).
5. `STATIC-001`'s "exactly three" experience slots was implied as fixed database columns. Closed by V2-D024 (`ExperienceSlot` is a related, sequenced collection; cardinality is a validation rule, not a schema shape).
6. M3's two very different-effort components (mostly-prebuilt static profile vs. 100%-new five-bucket context) were not distinguished in the implementation plan. Closed by V2-D025 (M3A/M3B split).
7. FACT-003's "warnings, not automatic rejection" did not distinguish which checks may still hard-fail. Closed by V2-D026 (explicit HARD_INTEGRITY / SOFT_REVIEW_WARNING split).
8. No quality-benchmark methodology existed. Closed by `docs/QUALITY_BENCHMARK.md`.

Where a numbered planning gap above is closed by a specific decision, see `docs/DECISIONS.md` for the authoritative decision text — this audit file is not updated further as decisions evolve.
