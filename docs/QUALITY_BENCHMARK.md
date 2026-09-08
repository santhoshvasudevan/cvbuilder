# CVBuilder V2 Quality Benchmark Methodology

**Status:** Proposed methodology, APPROVED at M0.2 (V2-D028)
**Date:** 2026-09-08

## 1. Purpose

Deterministic tests (`docs/TEST_STRATEGY.md` §1) verify structural correctness. They do not verify that CVBuilder's output is actually good. This document defines how content quality is measured, so the §1.1 success definition in `requirements.md` ("at least 90% of expert-assisted resume quality") is evaluated consistently rather than judged ad hoc.

This document is a methodology + running record. It does not itself contain fabricated benchmark results — entries are populated only when the corresponding source material and generated output actually exist.

## 2. What Gets Compared

For each benchmark case (one target job application), up to three outputs are compared side by side:

1. **Expert-assisted reference** — the best resume content a skilled human (recruiter/career coach/the candidate working carefully) would produce for this job, using the same candidate background. This is the quality ceiling, not a CVBuilder output.
2. **V1 agent output** — if a V1 (`main`-branch) run exists for the same job/candidate pair, its output is recorded for comparison. Not all benchmark cases will have a V1 counterpart, since V1 may never have been run against every case.
3. **V2 output** — CVBuilder V2's structured `ResumeDraft` + rendered markdown for the same job, at whatever pipeline stage of maturity currently exists (one-shot baseline during early M6, full multi-pass output once AB-1..4 are implemented).

A benchmark case is not required to have all three; a case with only a V2 output and no reference/V1 comparison is still recorded, but cannot yet be scored against the 90% target.

## 3. Measured Dimensions

Score each available output 1–10 on:

- **Positioning** — how compellingly does the resume argue candidate fit, not just list matched facts?
- **Differentiation** — does it surface what makes this candidate unusual/strong, not generic?
- **JD alignment** — does it address the specific job's actual requirements and recruiter priorities?
- **Specificity** — concrete evidence/metrics/technologies vs. vague claims?
- **Completeness** — is relevant candidate breadth used, not just the most recent role?
- **Career narrative** — does the experience read as a coherent progression?
- **Seniority positioning** — does the candidate read at the correct seniority level for the target role?
- **Technical credibility** — is technical depth demonstrated convincingly?
- **Recruiter impact** — what would a recruiter conclude in 15 seconds?
- **Generic wording** (inverted — lower is better) — how much boilerplate/filler language is present?
- **Factual correction burden** — how much operator editing was required before the content was trustworthy and accurate (fewer required corrections score higher)?

An overall score is the mean of the above (with "generic wording" and "factual correction burden" inverted before averaging, since lower raw values are better for those two).

## 4. Scoring Process

1. A human reviewer (the candidate/product owner, or a delegated reviewer) scores each available output independently, without being told which system produced which output where practical (blind comparison reduces anchoring).
2. Scores and rationale are recorded per dimension, not just as a single number, so disagreements or surprising results can be traced to a specific dimension.
3. `docs/TEST_STRATEGY.md` §5 (Token Evaluation) fields — input/output/cached tokens, latency, retries, stage, provider, model, reasoning level, configured output budget — are recorded alongside the quality scores for every V2 output, so quality-per-token comparisons are possible once enough benchmark cases exist (feeds M8).

## 5. Acceptance Interpretation

Per `requirements.md` §1.1 and `docs/DECISIONS.md` V2-D020, V2 is not considered content-quality complete until representative benchmark cases reach **approximately 90% of the expert-assisted reference's overall score** (i.e., V2's mean score ÷ reference's mean score ≥ ~0.90), evaluated across a representative set of benchmark cases (see M8's representative role list in `docs/IMPLEMENTATION_PLAN.md`), not a single case. A single strong benchmark case does not satisfy this criterion; a single weak one does not disqualify the system either — the target is evaluated in aggregate once enough cases exist.

## 6. Benchmark Case Record Template

Each benchmark case is recorded using this structure:

```text
Benchmark Case: <short name>
Target job: <employer / role / posting source>
Candidate background source: <which candidate documents / memory snapshot version>
Date recorded: <date>

Outputs available:
  - Expert-assisted reference: <yes/no — link or "not yet produced">
  - V1 agent output: <yes/no — link or "not applicable/not run">
  - V2 output: <yes/no — link or "not yet produced", plus which pipeline stage of maturity>

Scores (1-10 per dimension, per available output):
  <table: dimension x output>

Notes:
  <qualitative observations, disagreements, surprises>
```

## 7. Current Benchmark Cases

### Amazon — GenAI Solutions Architect

**Status: case identified, not yet scored.** This is recorded as the first representative benchmark case per V2-D028, but as of M0.2 (2026-09-08) no expert-assisted reference, V1 output, or V2 output exists yet for this specific job posting in this repository or in the candidate source documents reviewed during the M0.2 audit (`docs/AC/AC-MEMORY_PROFILE.md`, `AC-profile_english.md`, `AC-profile_german.md`, `docs/CANDIDATE_MEMORY_SNAPSHOT.md` — none reference an Amazon GenAI Solutions Architect application). Per V2-D028's explicit instruction not to invent unavailable source content, this entry is left as a placeholder:

```text
Benchmark Case: Amazon GenAI Solutions Architect
Target job: Amazon — GenAI Solutions Architect (posting source not yet captured)
Candidate background source: TBD — link the candidate memory snapshot/profile version used
Date recorded: pending

Outputs available:
  - Expert-assisted reference: no — not yet produced
  - V1 agent output: no — not yet run against this posting
  - V2 output: no — pipeline not yet implemented (M1 not started as of M0.2)

Scores: not applicable yet

Notes: Recorded here as a placeholder so this case is not lost. Populate once (a) the actual job posting text/URL is captured, (b) a candidate background source is designated, and (c) at least one of the three outputs exists.
```

This entry should be updated in place as real material becomes available — do not create a second "Amazon" entry.
