# Resume Output Structure (v1)

Status: resolved for v1 (see `requirements.md` §7, §16, and `docs/DECISIONS.md` D-007). This
document defines the structured resume representation Agent Builder produces, and the
deterministic markdown rendering contract applied to it. It is a **structural and content-design
reference** — it does not fix any candidate's actual factual content, and no example content
supplied during product-owner review has been hard-coded here or anywhere else in this
repository's planning documents.

This document satisfies AB-003/AB-004 (requirements.md §7) and the evidence-bearing structural
requirement in requirements.md §16 (traceability). It is the resolution of decision D-007 in
`docs/DECISIONS.md`.

## 1. Why structured-first, markdown-second

Agent Builder must not go straight to markdown. It first produces a structured representation
(defined below) where every factual element carries explicit evidence references
(`supporting_memory_claim_ids`) and, where relevant, the job requirements it addresses
(`matched_job_requirement_ids`). The no-fabrication validator (requirements.md §13, §16) runs
against this structured representation. Markdown is rendered from it only after that validation
passes. This keeps "did the LLM invent something" a machine-checkable question about IDs, and
keeps "does the wording read well and represent the evidence fairly" a human review question —
the two concerns are not conflated.

## 2. Structured representation

### A. Target positioning

```
TargetPositioning
    title_options[]          # multiple candidate target-title options considered
    recommended_title        # one recommended/final target title
```

### B. Professional summary

```
ProfessionalSummary
    elements[]                # each a ResumeElement (see below) — tailored summary statements
```

### C. Experience

```
ExperienceSection
    engagement_id              # references an APPROVED CareerEngagement record -- see D-019
    bullets[]                  # each a ResumeElement

ResumeElement (used throughout — experience bullets, summary statements, achievements, etc.)
    text
    supporting_memory_claim_ids[]     # required for every factual element
    matched_job_requirement_ids[]     # optional, where relevant
```

**The deterministic static-profile boundary (D-019, 2026-09-03):** `ExperienceSection` no longer
carries `employer`/`role_title`/`dates`/`location` fields directly — Agent Builder never generates,
rewrites, or even sees those values as part of its own structured output. It selects *which*
engagement a section is about (by `engagement_id`) and writes evidence-backed narrative bullets for
it; nothing else. The deterministic renderer (§4 below) resolves the employer, title, location, and
dates for that section exclusively from the referenced `CareerEngagement` record via
`services/static_profile_boundary.render_engagement_header` — never from anything Agent Builder
produced. An `engagement_id` that does not exist, or that is not operator-`APPROVED`, fails
validation outright rather than rendering with a placeholder or falling back to generated text (see
`services/static_profile_boundary.py`, `docs/DECISIONS.md` D-019).

Each factual bullet must contain explicit supporting `MemoryClaim` IDs. Where useful, a bullet
also references the `JobRequirement` IDs it's intended to address.

### D. Top strengths / positioning themes

```
PositioningTheme
    theme_text
    supporting_memory_claim_ids[]
```

Represents the strongest role-specific positioning themes identified for the candidate. Useful for
human review and for Agent Builder's own reasoning, even where not rendered as an explicit final
resume section. Every factual strength must remain evidence-backed like any other `ResumeElement`.

### E. Key achievements

```
Achievement
    text
    supporting_memory_claim_ids[]
```

A structured set of particularly strong achievements relevant to the target role. The final
markdown renderer avoids unnecessary duplication when an achievement already appears naturally
under Experience (see §4 below).

### F. Skills

```
SkillCategory
    category_name              # e.g. technical/domain, architecture/platform/integration,
                                # cloud/data/DevOps, non-technical/leadership/delivery
    skills[]                   # each backed by supporting_memory_claim_ids

SelectedResumeSkills
    skills[]                   # the final selected skills for THIS job/resume, drawn only from
                                # categorized skills above
```

A skill must not be added merely because it appeared in the job description — resume skills must
be supportable by `CandidateMemory`, i.e. every entry in `SelectedResumeSkills` must trace to a
confirmed `MemoryClaim` the same way any other `ResumeElement` does.

### G. Certifications

```
Certification
    text
    supporting_memory_claim_ids[]
```

### H. Languages

```
LanguageProficiency
    text
    supporting_memory_claim_ids[]
```

### I. Positioning / content guidance

```
PositioningGuidance
    preferred_role_positioning
    do_not_overstate[]
    terminology_preferences[]
    context_only_technologies[]   # technologies to describe as collaboration/context rather
                                   # than hands-on development
    naming_privacy_preferences
    resume_language
```

This carries explicit per-job/operator guidance that constrains *how* Agent Builder generates
content. It is a generation constraint, not independent factual evidence — no specific guidance
value is a global candidate fact, and none from any example supplied during product-owner review
is hard-coded as a default here.

## 3. Structured output validation (before rendering)

Per requirements.md §16, before any markdown is produced, the no-fabrication validator checks:

1. Every referenced `MemoryClaim` ID exists.
2. Every referenced `MemoryClaim` is `confirmed`.
3. Every claim belongs to an eligible `CandidateMemory` revision.
4. Every claim is allowed for this `FitAssessment`/job-application context.
5. Every factual resume element (bullets, summary statements, achievements, skills,
   certifications, language entries) contains at least one evidence reference.
6. No evidence ID was fabricated by the LLM (i.e., every ID resolves to a real row).
7. **(D-019)** Every `ExperienceSection.engagement_id` resolves to a `CareerEngagement` that exists
   **and** is operator-`APPROVED` (`services/static_profile_boundary.resolve_approved_engagement`)
   — an unknown or unapproved ID fails validation before any markdown is rendered, exactly like a
   fabricated `MemoryClaim` ID.

This is an eligibility/attachment check, not a semantic-similarity check (requirements.md §16
explicitly rules out exact/near-text or embedding similarity as the fundamental truth test).
Human review of wording quality and fairness happens afterward, at Human Review Gate 2.

## 4. V1 markdown rendering contract

Once the structured representation passes validation, it is rendered deterministically into this
markdown structure:

```markdown
# [Recommended Target Title]

## Professional Summary

[rendered ProfessionalSummary elements]

## Professional Experience

### [rendered via services/static_profile_boundary.render_engagement_header(engagement_id) — role, organisation, dates, and location come only from the referenced APPROVED CareerEngagement record, D-019]
- [bullet]
- [bullet]
...

## Key Skills

[concise SelectedResumeSkills representation]

## Certifications

[rendered Certification entries, omitted if none]

## Languages

[rendered LanguageProficiency entries, omitted if none]
```

The renderer omits internal planning sections from the final markdown — these remain inspectable
structured Agent Builder data for operator review, but are not rendered:

- title alternatives (`TargetPositioning.title_options`)
- top-strength analysis (`PositioningTheme`)
- positioning notes (`PositioningGuidance`)

Achievements are not rendered as a separate top-level section by default — the renderer must avoid
duplicating an achievement that already appears naturally under Experience. If a `KeyAchievement`
is not naturally covered by any Experience bullet, it may be rendered under Experience for the
relevant role rather than introducing a redundant standalone section, to keep the v1 rendering
contract exactly as listed above. (Revisiting a standalone Achievements section is left open for a
future iteration if operator experience shows the collapsed approach loses useful signal.)

## 5. What this document deliberately does not do

- It does not fix exact resume wording, section ordering nuances beyond the contract above, or
  any candidate-specific content — those remain Agent Builder's generation responsibility within
  this structure, and the operator's review responsibility at Gate 2.
- It does not weaken the no-fabrication invariant — structured elements are exactly what the
  validator in §3 checks before rendering is ever allowed to run.
- It does not implement PDF/DOCX rendering (still out of v1 scope per requirements.md §1/§14).
