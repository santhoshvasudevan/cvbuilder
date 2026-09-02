# Requirements: Agentic Resume Generation App

Status: Draft v1 — for review
Owner: Santhosh Kumar Vasudevan
Related project: `career-intelligence` (this repo) — architectural lessons cited throughout are drawn from it, not copied wholesale

## 1. Overview & Goals

This document specifies a new, standalone application (built from scratch, in its own repository/folder — no shared code with `career-intelligence` at this stage) whose purpose is:

> Given a job requirement and a candidate's career background, produce a truthful, strongly-positioned, tailored resume.

The app is **agentic**: the work is done by a small pipeline of cooperating agents, each backed by one or more LLM calls, with a human reviewing and approving the output at every meaningful checkpoint rather than the pipeline running end-to-end unattended. The LLM provider behind each call must be independently configurable — starting with OpenAI, NVIDIA NIM, and Google (Gemini), with more providers added later without changing pipeline logic.

This is a **from-scratch, exploratory build**. It intentionally does not yet have deterministic/repeatable development practices (e.g. recorded-response test suites) — those come later, once the pipeline produces consistently good output and is worth locking down. This document should not be read as mandating a finished, production-hardened system; it defines the shape of a v1 that is honest about what's deferred.

### Non-goals for v1

- No PDF/DOCX rendering — output is a markdown file only.
- No multi-tenant auth or multi-user support — single local operator, single candidate.
- No deterministic/recorded-response test suite — noted as a future phase (Section 12).
- No message broker (Redis/Celery) or background worker infrastructure.
- No cloud deployment — local-first only.

## 2. Actors & Personas

There is exactly one human actor in v1: **the operator**, who is also the candidate. They run the app locally, paste or point it at a job posting, review each agent's output, give feedback, and approve or reject before the pipeline continues. There is no separate "recruiter" or "reviewer" role — the pipeline's agents *simulate* a recruiter's perspective (see Agent Jobber, Section 5), but no second human is involved.

This mirrors `career-intelligence`'s own operating model: a local-first, single-operator tool, not a multi-tenant product. That precedent is why Section 11 recommends against building auth, multi-tenancy, or cloud infrastructure until an actual second user exists.

## 3. Pipeline Overview

```
Prerequisite (once, revisited as needed)
  Candidate markdown docs  →  [Memory Build]  →  Candidate Memory (structured, versioned)

Per job application
  Step 1: Agent Jobber (AJ)
    Job posting (URL or pasted text) → [Parse + recruiter-lens analysis] → Job Requirement Analysis
                                                                                  │
  Step 2: Agent Candidate (AC)                                                  ▼
    Job Requirement Analysis + Candidate Memory → [Retrieve relevant facts, assess fit/gaps] → Fit Assessment
                                                                                  │
                                                                  ── HUMAN REVIEW GATE 1 ──
                                                     (sees AJ + AC output together, can send
                                                      feedback back to AJ or AC, re-run either)
                                                                                  │
  Step 3: Agent Builder (AB)                                                    ▼
    Job Requirement Analysis + Fit Assessment (+ human edits) → [Position + draft] → Resume Draft (markdown)
                                                                                  │
                                                                  ── HUMAN REVIEW GATE 2 ──
                                                    (sees rendered draft, can give content feedback,
                                                     triggers regeneration incorporating it)
                                                                                  │
                                                                                  ▼
                                                                     Final Resume (markdown file)
```

Each arrow labeled `[...]` is one or more LLM calls. The provider/model behind every one of those calls is independently configurable (Section 9).

## 4. Prerequisite: Candidate Memory Build

This step is done once (and revisited/updated over time, not per job application). It does not exist yet in the new app and must be built — unlike `career-intelligence`, which already has a working `memory_profile` app to model this after.

**Input**: one or more markdown files supplied by the candidate, containing free-form career history — roles, projects, achievements, quantified impact, challenges overcome, skills, education, etc. No fixed format is imposed on the source docs; the agent's job is to make sense of unstructured input.

**Processing requirement**: an LLM call (or set of calls) reads the source markdown and organizes its content into a **structured memory**, keyed by the resume sections it will eventually be used to populate (e.g. Summary, Experience — per role, Skills, Achievements, Education). Each extracted item should retain a pointer back to its source document/location so provenance is never lost.

**Output shape**: a `CandidateMemory`, composed of many small `MemoryClaim` records rather than one large blob — this makes later retrieval (Step 2) precise (Agent Candidate should pull only the claims relevant to a given job, not the whole profile) and makes human review/correction tractable at the level of a single fact.

**Review & versioning requirement**: because this memory becomes the sole factual source for every future resume, it needs the same trust discipline `career-intelligence`'s `memory_profile` app applies:

- Every `MemoryClaim` has a `confirmation_status` (`unconfirmed` / `confirmed` / `retired`). Only `confirmed` claims are eligible for use in Steps 2–3.
- The memory-build step is a **separate trust track from raw fact storage** — it organizes and phrases, it does not invent. If the source docs don't support a claim, the agent must not synthesize one to fill a gap.
- Memory content is versioned (new markdown supplied later creates a new revision, old revisions are kept, not overwritten) so a candidate can see how their profile evolved and roll back a bad extraction.

This mirrors the exact separation `career-intelligence` draws between `apps.evidence` (verified facts) and `apps.memory_profile` (narrative/positioning claims derived from those facts) — the same discipline applies here even though this app has only one source (the markdown docs) rather than two.

## 5. Step 1 — Agent Jobber (AJ)

**Input**: a job posting, supplied either as a **URL** (the app fetches and extracts the posting content) or as **pasted text**. Both intake methods are required for v1.

**Processing requirements**:
- Detect and work in the job posting's own language — do not assume English.
- Adopt a recruiter's mindset for the specific role, not a generic keyword extractor.
- Identify and separate: mandatory requirements, preferred/nice-to-have requirements, core responsibilities, keywords likely used in ATS screening, screening risks (things that might get a candidate filtered out), and implied expectations not stated outright (e.g. seniority signals, team context, unstated tooling assumptions).

**Output**: a structured `JobRequirementAnalysis` record capturing all of the above, tagged with `source_type` (`url` or `pasted`) and the original raw text/URL for traceability.

**Risk note — URL fetching**: fetching arbitrary job-posting URLs is inherently brittle — many boards render content via JavaScript, block scrapers, or change markup frequently. V1 must fetch on a best-effort basis and **always allow falling back to pasted text** when a fetch fails or produces unusable content; the UI should surface fetch failures clearly rather than silently producing a low-quality analysis from a broken scrape. Treat scraping robustness as an ongoing maintenance concern, not a one-time build.

## 6. Step 2 — Agent Candidate (AC) — Matching

**Input**: the `JobRequirementAnalysis` from Step 1, plus the `CandidateMemory` from the prerequisite step.

**Processing requirements**:
- Retrieve **only** the memory claims relevant to this specific job — not the candidate's entire history. This keeps later prompts smaller and keeps the positioning step (AB) focused.
- Evaluate genuine fit: what matches well, and — just as importantly — what's missing. **The agent must not conceal or minimize genuine gaps.** A resume built on a dishonest fit assessment is worse than useless; the human reviewer needs the real picture to decide how (or whether) to proceed.

**Output**: a `FitAssessment` — matched requirements with supporting evidence (which memory claims satisfy which job requirement), explicitly listed gaps, and any risk notes carried over from AJ's screening-risk analysis.

**Human Review Gate 1**: before proceeding to Agent Builder, the UI must show the operator AJ's output and AC's output **together**, in readable form. The operator can:
- Approve, proceeding to Step 3, or
- Send feedback to either AJ or AC (e.g. "this requirement was mis-read," "this claim doesn't actually apply") and trigger a re-run of that step with the feedback incorporated.

This gate is a **database precondition, not a paused agent/graph state** — see Section 10 for why this specific design choice matters and where it comes from.

## 7. Step 3 — Agent Builder (AB)

**Input**: `JobRequirementAnalysis`, the (possibly human-edited) `FitAssessment`, and the underlying `CandidateMemory` claims it references.

**Processing requirement**: select the strongest **truthful** positioning of the candidate against this specific job — deciding what to lead with, what to de-emphasize, and how to phrase achievements — without introducing any claim not traceable to a confirmed memory claim. This is a hard invariant, not a style preference (see Section 13).

**Output**: a markdown resume file in a defined structured format. (The exact section template — headings, ordering, formatting conventions — should be defined as a short appendix to this document or a separate template file once drafted; it is a content-design task, not an architectural one, and is left open here.)

**Human Review Gate 2**: the UI renders AB's output in readable form (rendered markdown, not raw source). The operator can give content feedback (wording, emphasis, corrections) which triggers regeneration incorporating that feedback. Once approved, the markdown file is the final deliverable for this job application.

**Output format decision**: markdown only for v1. Rendering to PDF/DOCX is explicitly deferred (Section 14) — proving content quality comes first.

## 8. Data Model (sketch)

This is a starting sketch, not a final schema — field-level detail should be worked out during implementation. Entities:

- **`CandidateMemory`** — versioned container; **`MemorySourceDocument`** — one uploaded markdown file, linked to a memory version; **`MemoryClaim`** — one extracted fact/achievement, with `resume_section`, `confirmation_status`, `source_document` FK, and pointer/quote back to source text.
- **`JobRequirementAnalysis`** — `source_type` (url/pasted), raw input, structured AJ output (mandatory/preferred requirements, responsibilities, keywords, risks, implied expectations).
- **`FitAssessment`** — FK to `JobRequirementAnalysis`, matched-claims list (FK to `MemoryClaim`), gaps list, risk notes.
- **`ReviewFeedback`** — free text + target step (`AJ`/`AC`/`AB`), FK to whichever record it's feedback on, timestamp, triggers a re-run when submitted.
- **`ResumeDraft`** — FK to `FitAssessment`, markdown content, `status` (`draft` / `awaiting_review` / `confirmed`), `confirmed_at`.
- **Provider registry tables** — see Section 9.
- **`LLMCallLog`** — one row per LLM call: provider, model, pipeline stage, token usage, latency, retry count, error category (sanitized — never raw response bodies). This is an audit ledger, not a cache.

## 9. LLM Provider Abstraction (core requirement)

Interchangeable providers are a first-class requirement, not an afterthought — every LLM call in the pipeline (memory build, AJ, AC, AB) must be able to run against a different provider/model independently, and adding a new provider must not require touching pipeline logic.

### 9.1 Adapter interface

Define a single adapter interface that every provider implements: takes a normalized request (prompt/messages, output schema, generation parameters) and returns a normalized result (content, usage metadata, or a typed error). Pipeline code calls this interface only — it never talks to a provider SDK directly. This is the same shape `career-intelligence` converged on (`AIAdapter` in `apps/matching/ai.py`), and keeping pipeline logic decoupled from any specific SDK is what makes provider-swapping actually cheap later.

### 9.2 Provider/model registry — DB-backed, admin-editable

Unlike `career-intelligence` (which resolves provider/model purely from environment variables and code-level allowlists), this app's registry lives in Postgres and is editable through a simple admin UI, since "interchangeable per call" is a literal, day-to-day requirement here rather than an occasional deploy-time config change:

- **`LLMProvider`** — name, base URL, auth-credential reference (the credential value itself stays in an env var/secret store, never in the DB row).
- **`LLMModel`** — FK to provider, model identifier, capability flags (structured-output support, streaming support, reasoning/thinking support, max output tokens).
- **`StageModelAssignment`** — maps a pipeline stage (`MEMORY_BUILD`, `AJ_ANALYZE`, `AC_MATCH`, `AB_BUILD`, ...) to a chosen `LLMModel`. Changing which provider handles a given stage is an admin-UI edit, not a code change or redeploy.

### 9.3 Structured output — known per-provider risks

Every stage needs the LLM to return data matching a schema (not free text), and each provider handles this differently. These are documented, previously-hit issues worth designing around from day one rather than rediscovering:

- **OpenAI**: supports strict JSON-schema-constrained output directly.
- **NVIDIA NIM** (OpenAI-compatible chat-completions): supports `response_format`-style strict JSON schema, but as a self-hosted/managed endpoint its available models and exact compatibility should be verified per model.
- **Google Gemini**: requires its own reduced schema dialect — no `$ref`/`$defs`, proto-style enum types. Critically, **accumulating too many `enum` constraints across schema fields has been observed to cause opaque 400 errors** — the safe pattern is to drop `enum` constraints from the schema sent to Gemini and instead re-validate the returned values against the allowed set server-side after the fact. Gemini's gRPC transport has also been observed to reject payloads that succeed over REST with the exact same content — **pin REST transport for Gemini calls**. Gemini's "thinking budget" / reasoning-effort parameters are still evolving across model generations and should be treated defensively (e.g. a "disabled reasoning" setting may not accept a literal zero value).

New providers should be assumed to have their own such quirks; the adapter interface should make it easy to isolate provider-specific translation logic in one place per provider.

### 9.4 Retry policy

Retry only on transient failures (rate limits, 5xx server errors) — not on validation or auth failures. For streaming calls, **retry only if no output has streamed yet**; never retry a call that partially streamed, to avoid producing duplicated or inconsistent output. Cap retry attempts and back off between them.

### 9.5 Error handling

Normalize provider errors into a small typed taxonomy (e.g. configuration, auth, rate-limit, timeout, schema-validation, provider-internal). Never let a raw provider exception message or response body reach a log line or a stored DB field unsanitized — provider errors can echo request/response content, which may include candidate data.

### 9.6 Audit logging

Every LLM call writes an `LLMCallLog` row (Section 8) — provider, model, stage, token usage, timing, retry count, error category. This is cheap to build now and becomes the primary debugging tool once the pipeline has more than one stage and more than one provider in play, which it will from day one here.

### 9.7 Future: shared library candidate

This provider-adapter layer (interface, per-provider quirk handling, retry/error taxonomy) is intentionally similar in spirit to what `career-intelligence` already built. It is **not** to be shared or extracted now — this app's codebase is fully independent. But if a third app with the same need ever appears, this layer is the natural candidate to extract into a shared internal package. Flagging this now is just so the interface is kept clean enough that extraction later wouldn't require a rewrite.

## 10. Human-in-the-Loop UX Requirements

Both review gates (Section 6, Section 7) must be implemented as **plain application state — an ordinary status field on a database row — checked at the start of the next step, not as a paused agent/orchestration-graph waiting to be resumed.**

This is a direct, deliberate borrow from `career-intelligence`'s ADR-0014, which considered and explicitly rejected the alternative (an orchestration-framework "interrupt and resume" primitive) for reasons that apply here without modification:

- A human review pause can last minutes or days. A simple "check a status column when the next step starts" design handles that for free; a paused, resumable execution state needs its own answer to "has anything gone stale while this was paused?" — and by re-checking from a fresh DB read every time, the precondition approach gets that answer automatically.
- Approval becomes a generic, reusable action (a Django view that flips a status and records who/when) rather than something coupled to a specific pipeline's internal execution state.
- It avoids an entire class of operational problems — orphaned or stuck "paused" runs — that a framework-level resume mechanism introduces and then has to be babysat.

Concretely: when the operator submits feedback at a review gate, that's a normal database write (a `ReviewFeedback` row, plus updating the target record's status back to something like `needs_rework`). The next pipeline run for that stage checks status fresh and either proceeds or re-runs with the feedback as additional input — no long-lived execution thread is kept waiting.

Whenever upstream context changes after a draft was created (e.g. the operator edits the `FitAssessment` after Agent Builder already drafted a resume from the old version), the next step must re-validate freshness rather than silently building on stale input.

## 11. Tech Stack Recommendation

| Concern | Recommendation | Why |
|---|---|---|
| Backend framework | Django | Given/requested; also what `career-intelligence` uses successfully at this same scale. |
| Database | PostgreSQL, run via Docker locally | Given/requested; needed as the durable system of record for every stage's output, review state, and the LLM call audit log — this is not optional even though the app is "just markdown out." |
| Background jobs / queue | **None for v1** — run LLM calls synchronously inside the Django view/request that triggers them | Single operator, one job in flight at a time, and every step already pauses for human review anyway — a few seconds of blocking latency per LLM call is not a real cost. Matches `career-intelligence`'s own explicit principle: *"Introduce queues, Redis, async workers, or a separate frontend only when a milestone requires them."* Add a simple DB-row-claiming worker (no broker needed) later only if the UI needs to stay responsive during a long call. |
| Frontend | Server-rendered Django templates | No SPA/JS framework needed at single-operator scale; keeps the stack small. This is what `career-intelligence`'s "operator UI" is built with, successfully. |
| Schema validation | Pydantic (or Django forms/serializers, but Pydantic is a better fit for LLM output validation specifically) | Needed to validate structured LLM output against the expected schema before it's trusted/stored. |
| Orchestration | Optional: a lightweight state-machine/step-sequence of your own, OR LangGraph if checkpointed multi-step state and resumability-after-crash is valuable | Not mandated either way. `career-intelligence` uses LangGraph successfully, but a from-scratch app with only 4 stages and DB-precondition-based human gates (Section 10) may not need a graph framework's complexity at all — a plain ordered sequence of Django-view-triggered functions may be simpler and just as correct. Decide once the pipeline is prototyped; don't adopt LangGraph by default. |
| Secrets | `.env` file, never committed; provider auth credentials referenced (not stored) from the DB registry | Standard practice; also matches how `career-intelligence` handles it. |
| Deployment | Local-first only — no Dockerfile/CI required to start, though adding a minimal CI (lint + tests) early is cheap and worth doing even for a local-first app | Matches the confirmed single-user, local-first deployment model. |

## 12. Testing Strategy (phased)

**Phase 1 (now)**: no deterministic/recorded-response test suite yet, by explicit decision — the pipeline's prompts and logic are still being iterated on, and locking down "golden" LLM responses this early would just create tests that need constant rewriting. Ordinary unit tests for non-LLM logic (schema validation, the retrieval step in Agent Candidate, review-gate state transitions) are still worth writing from day one.

**Phase 2 (once outputs stabilize)**: build a recorded-response ("cassette") testing layer — record real provider responses once, replay them in CI thereafter, so tests are fast, free, and deterministic without needing live API keys. `career-intelligence`'s `evals/cassette_store.py` is a proven pattern for this (hash the request, store/replay a JSON fixture, forbid live "record" mode in CI). The reason to plan for this now, even while deferring it: **design the provider adapter interface (Section 9.1) as the one seam all LLM calls pass through**, so that bolting on request/response recording later is a small addition at that one seam, not a rewrite of every pipeline call site.

## 13. Non-Functional Requirements

- **No-fabrication invariant**: every claim that ends up in a generated resume must trace back to a `confirmed` `MemoryClaim`. This is non-negotiable and should be enforced by validation after generation (check that AB's output only references known claims), not just by prompting.
- **No-concealment invariant**: Agent Candidate's gap analysis (Section 6) must surface genuine mismatches; the pipeline must never quietly hide a gap to make the fit look better than it is.
- **Secrets management**: provider API keys/credentials live in environment variables or a secrets file, never in the database or version control.
- **Cost/token visibility**: the `LLMCallLog` (Section 8) should be enough to answer "how many tokens/dollars did this job application cost, broken down by stage and provider" without extra instrumentation.
- **Extensibility**: adding a new LLM provider should mean (a) implementing the adapter interface for it, and (b) adding rows to the provider/model registry — zero changes to AJ/AC/AB pipeline logic.

## 14. Open Questions / Future Phases

- Exact markdown resume template/structure (headings, section order) — a content-design decision, not covered here.
- PDF/DOCX rendering of the final resume.
- Deterministic/cassette-based test suite (Section 12, Phase 2).
- Multi-candidate support, if this ever needs to serve more than one person's profile.
- Hardening URL-fetch reliability (headless rendering for JS-heavy job boards, retry/backoff on fetch failures) — deferred until real-world fetch failure rates are observed.
- Streaming UI feedback while a generation is in progress (currently out of scope given synchronous, single-call-at-a-time execution).

## 15. Appendix: Lessons Carried from `career-intelligence`

This section exists so the rationale behind several decisions above isn't lost to "because I said so." Each of these was a real, previously-solved problem in `career-intelligence`, cited here rather than re-derived from scratch:

- **Gemini structured-output fragility** (Section 9.3): schema dialect differences, enum-accumulation causing opaque 400s, and gRPC-vs-REST transport divergence were all root-caused and fixed in `apps/matching/langchain_runtime.py`. Expect the same class of issues here and design the Gemini adapter defensively from the start.
- **Retry-only-if-nothing-streamed** (Section 9.4): a real bug class in streaming APIs — retrying after partial output causes duplicated/garbled results. Fixed in the same file; carry the rule forward, don't rediscover it.
- **Approval as a DB precondition, not a graph interrupt** (Section 10): explicitly decided in `docs/adr/0014-intake-generation-precondition-gate.md` after seriously considering the alternative. The reasoning in that ADR — staleness handling, avoiding orphaned paused state, keeping approval generic and reusable — applies directly to this app's two review gates.
- **Memory as a separate trust track from raw facts** (Section 4): `apps/memory_profile`'s design (claims with `confirmation_status`, never a new source of facts, only phrasing/prioritization) is the model for how `CandidateMemory` should relate to the source markdown docs here.
- **Stack minimalism**: `AGENTS.md` in `career-intelligence` states plainly — *"Introduce queues, Redis, async workers, or a separate frontend only when a milestone requires them."* This is the direct justification for Section 11's "no Redis/Celery, no SPA" recommendation.
- **Audit ledger pays for itself early**: `career-intelligence`'s `AIWorkflowRun`/`AIWorkflowStage` models made debugging a multi-provider, multi-stage pipeline tractable. Section 9.6's `LLMCallLog` requirement is the same idea, scoped down for this app's needs.
