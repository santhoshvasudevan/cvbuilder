# Operator Fact Resolutions

This file is an **operator-approved `OPERATOR_UPDATE` evidence source** (D-015,
`docs/DECISIONS.md`), with precedence **above** `AC-MEMORY_PROFILE.md` for the specific facts it
addresses. It does not invalidate anything in `AC-MEMORY_PROFILE.md`, `AC-profile_english.md`, or
`AC-profile_german.md` beyond the exact statements it explicitly resolves. This file is
append-only: new dated resolutions are added below prior ones, never replacing or deleting
earlier entries, so the resolution history stays auditable.

Precedence order (highest to lowest) after this file exists:

1. `AC-OPERATOR_FACT_RESOLUTIONS.md` (this file) — for the specific facts it addresses only.
2. `AC-MEMORY_PROFILE.md` — primary curated profile; still highest-precedence for everything this
   file does not address.
3. `AC-profile_english.md`
4. `AC-profile_german.md`

---

## Resolution set 2026-09-02

### 1. Employment structure — legal employer vs. client

Ambigai Consultancy Services was the legal employer of record for the candidate's Ford and
Continental client assignments referenced elsewhere in this profile. Where the source corpora
describe direct employment at "Ford Motor Werk GmbH" or "Continental Automotive," those are
**client organizations** the candidate was assigned to work with, not the legal employer.

PostgreSQL Candidate Memory must preserve **both** facts distinctly for each such assignment:
the legal employer (Ambigai Consultancy Services) and the client organization (Ford or
Continental), as separate, queryable fields — never collapsed into a single "employer" value that
loses one side of the relationship.

### 2. Default resume presentation

Resume output for these assignments defaults to **client-centric presentation** — leading with
the client organization name (Ford or Continental), since that is what is recognizable and
relevant to a reader, while remaining truthful about the actual relationship. Example default
phrasing:

- "Ford Motor Company — [Role] (client assignment)"
- "Continental Automotive — [Role] (client assignment)"

The operator may request, through the Candidate Memory / resume UI, one of three presentation
modes for these assignments:

- `CLIENT_CENTRIC` (default) — client name leads, relationship stated truthfully.
- `LEGAL_EMPLOYER_EXPLICIT` — legal employer (Ambigai Consultancy Services) stated explicitly and
  prominently alongside the client.
- `COMBINED` — both legal employer and client presented together with equal prominence.

No presentation mode may omit or misrepresent the underlying legal-employer/client relationship;
the modes differ only in emphasis and ordering, never in truthfulness.

### 3. Ford client assignment — start date

The Ford client assignment (via Ambigai Consultancy Services) began in **November 2017**.

This resolution addresses the **start date only**. Do not infer or invent an end date from this
resolution. Any separately, explicitly supported current/ongoing status, assignment
subdivisions (e.g. distinct engagement phases within the overall Ford assignment), or gaps must
continue to be represented according to their own provenance and the normal conflict-detection
rules — this resolution neither confirms nor denies them.

### 4. Continental client assignment — dates and location

The Continental client assignment (via Ambigai Consultancy Services) ran from **July 2015 through
October 2017**.

This resolution **supersedes** conflicting statements elsewhere in the source corpora that give a
July 2017 end date for the Continental assignment. The correct end date is October 2017.

The Continental assignment location was **Nuremberg, Germany**. This resolution supersedes
conflicting references elsewhere in the source corpora that place this assignment in Frankfurt am
Main.

### 5. Maruti Suzuki — start date

The Maruti Suzuki India Pvt. Ltd. experience began in **August 2012**. This resolution supersedes
conflicting statements elsewhere in the source corpora giving a September 2012 start date. August
2012 is correct.

### 6. German language proficiency

Current German language proficiency is **B1**.

B2 is actively being pursued (in progress) but has **not yet been attained**. Do not represent
current proficiency as B2 or higher, and do not represent B1 as the final target level — both the
current level (B1) and the in-progress pursuit of B2 must be preserved as distinct, separately
supported facts.

**Fit-assessment guidance for downstream Agent Candidate use** (not itself a resume claim, but a
rule governing how the B1/B2 facts above may be used when matching against a job requirement):

- If a job requirement mandates **current** B2 German proficiency, Agent Candidate must **not**
  classify this as `MATCH`. The correct disposition is normally `GAP`, with the candidate's active
  B2 study explicitly noted as a mitigating factor in the gap explanation — never silently upgraded
  to `MATCH` because study is in progress.
- If a job requirement lists B2 German as **preferred** (not mandatory), `PARTIAL` may be an
  appropriate disposition, with the B1-current/B2-in-progress limitation stated explicitly in the
  disposition's explanation — never presented as if B2 were already met.
