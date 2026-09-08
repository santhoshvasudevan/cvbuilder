# CVBuilder V2 Current State

**Date:** 2026-09-08  
**Branch:** `cvbuild2`  
**Branch baseline:** `fd02af8`

## Summary

The project is undergoing a V2 architectural redesign.

The current V2 direction is intentionally different from the earlier implementation:

- positioning quality is the primary generation objective;
- final factual approval belongs to the operator;
- Candidate Context is broader than direct matching claims;
- AJ gains a Recruiter Decision Model;
- AC is simplified and made more holistic;
- APS is introduced as a first-class Positioning Strategy artifact;
- AB becomes multi-pass: plan → draft → critique → refinement;
- final LLM-generated ResumeDraft is limited to titles, summary, three experience bullet sections, key achievements, and skills;
- company/date/title/location metadata is static;
- certifications/languages are static/deferred from generation;
- provider/model/reasoning/token settings are operator-visible for every call;
- OpenRouter joins OpenAI/NVIDIA/Gemini as an initial provider;
- main-branch code has been selectively reused after a completed read-only audit (`docs/V2_REUSE_AUDIT.md`).

## Implementation status

**Architecture closure is complete. M1 is authorized to begin next.** `main`'s reuse potential has been fully assessed and every architecture ambiguity/gap found during and after that assessment has been closed with explicit decisions (`docs/DECISIONS.md` V2-D021 through V2-D034). Do not infer that prior `main` implementation is present on `cvbuild2` — nothing has been merged or cherry-picked yet; only documentation has been updated. No candidate source/evidence documents, application code, or migrations have been touched during architecture closure.

Resolved in the M0.2 architecture-closure follow-up pass:

- `candidate_context` is a distinct Django app from `candidate_memory`; `candidate_memory` remains the sole source of candidate truth, `candidate_context` only builds a job-specific projection from it (V2-D030).
- `ExperienceSlot` selection is explicitly operator-controlled (select engagement → create/activate slot → order → edit metadata if authorized → validate exactly three) — never automatic (V2-D031).
- `ReviewFeedback` targets a `StageRun` (nullable, plus a `gate` field) instead of a separately-maintained `Target` enum (V2-D032).
- AJ's `JobRequirement` captures three orthogonal dimensions — `requirement_priority`, `requirement_domain`, `origin` — instead of one flat category enum (V2-D033).
- `LLMModel.supported_reasoning_levels` is the canonical source of a model's reasoning capability; `supports_reasoning` is derived only, never independently stored (V2-D034).

Resolved in the initial M0.2 pass:

- `JobApplication` redesigned as a small, stable aggregate; per-stage execution state moved to new `StageRun`/`JobApplicationStageState` models owned by `job_applications`, not a growing `current_*` FK list (V2-D022).
- `llm_provider` boundary clarified: it owns `LLMProvider`/`LLMModel`/`StageModelAssignment`/`LLMCallLog`/adapters only; `LLMCallLog` references the initiating `StageRun` (V2-D022).
- One canonical stage vocabulary defined, distinguishing LLM-capable stages from deterministic/workflow stages (V2-D022).
- Pipeline phase and application outcome confirmed/corrected as two separate enums, never merged into one status list (V2-D021).
- `FitExperienceLevel` (AC layer) formalized as distinct from `MemoryClaim`'s experience-level classification — no forced migration (V2-D023).
- `ExperienceSlot` modeled as a sequenced related collection with a HARD_INTEGRITY cardinality check, not fixed columns (V2-D024).
- M3 split into M3A (Candidate Knowledge + StaticResumeProfile — largely pre-built on `main`) and M3B (CandidateContextSnapshot — confirmed 100% new work), with M3B requiring its own quality acceptance gate before AC depends on it (V2-D025).
- Factual checking split into `HARD_INTEGRITY` (blocking) and `SOFT_REVIEW_WARNING` (advisory) classes (V2-D026).
- APS quality acceptance criteria required to pass before M6 (AB) implementation begins (V2-D027).
- Benchmark methodology defined in `docs/QUALITY_BENCHMARK.md`, with the Amazon GenAI Solutions Architect application as the first representative benchmark (V2-D028).
- Historical "old four-call AC chain" description corrected: V1 had three calls (`AC_NORMALIZE`/`AC_RANK`/`AC_MATCH`) in `candidate_matching` plus a separate `AB_BUILD` call in `resume_builder` (V2-D029).
- Four candidate source documents found accidentally deleted in the working tree (`docs/AC/AC-MEMORY_PROFILE.md`, `AC-profile_english.md`, `AC-profile_german.md`, `docs/CANDIDATE_MEMORY_SNAPSHOT.md`) were confirmed present and intact.

Remaining non-blocking implementation choices (do not block M1; settle during their respective milestones): whether `ExperienceSlot` metadata is copied at creation or resolved by reference to its source engagement record (M3A); exact stored enum naming for the AJ requirement taxonomy (M4).

The next correct action is:

1. begin M1 (Foundation and Reuse Audit implementation) per the closed design in `docs/ARCHITECTURE.md` §5 and `docs/IMPLEMENTATION_PLAN.md`;
2. cherry-pick `main` files per `docs/V2_REUSE_AUDIT.md`'s recommended reuse order, file-by-file with tests, never by bulk merge.
