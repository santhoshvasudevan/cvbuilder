GENERATED_REFERENCE_ONLY: true
RUNTIME_PROMPT_INPUT: false
CANDIDATE_MEMORY_EVIDENCE_SOURCE: false
REINGESTION_ALLOWED: false

# Candidate Memory Snapshot (Pre-M3 Reference)

**This is currently a pre-M3, manually-synthesized reference.** Once Milestone M3
(`candidate_memory`) exists, this document will instead be generated automatically from the
**active PostgreSQL Candidate Memory revision** (see `docs/DECISIONS.md` D-015 and
`docs/ARCHITECTURE.md` §4/§8/§9). Until then, it is a human-readable orientation document only.

**This document is not an evidence source, not authoritative runtime state, and not default LLM
context.** It must never be re-ingested as Candidate Memory evidence, and it must not be loaded by
default or passed to a pipeline prompt — see `CLAUDE.md`. The authoritative evidence is the three
source files listed in the manifest below; the authoritative *operational* memory, once it exists,
is the PostgreSQL `CandidateMemory` revision, not this file.

Canonical language: **English**. This document does not concatenate or reproduce the three source
files — it distills recurring, well-corroborated content and explicitly separates fact from
guidance. It also does not repeat the many alternative per-application summaries, titles, or
company-specific tailoring instructions found in the sources; those are positioning-plane content
(see `docs/ARCHITECTURE.md` §8), not canonical fact, and are out of scope for this reference.

---

## 1. Canonical career history

Corroborated across all three sources, in reverse-chronological order. Employment dates below are
the majority-consistent reading; see §7 for a genuine date/structure contradiction the sources do
not agree on.

- **Ford Motor Werk GmbH** — Köln, Germany — Product Owner / Solutions Architect, Connected
  Vehicle Cloud & Data Platforms — 07/2017–Present. Connected-vehicle cloud platform ownership
  (Fleet Telematics, Remote Services, User Authorization, Feature Enrollment, EV Charging
  Schedules, Subscription Services) spanning product ownership (SAFe/ART, PI Planning, backlog,
  acceptance criteria) and hands-on cloud/data engineering (Python, SQL, GCP, dbt, Airflow,
  Docker/Kubernetes, Terraform, CI/CD).
- **Continental Automotive** (Conti Temic microelektronik GmbH) — Nuremberg, Germany — System
  Engineer, Test Automation & Validation Platforms — 07/2015–07/2017. Safety-critical Transmission
  Control Unit software validation (ASIL-D), HiL test architecture (dSPACE, AutomationDesk,
  ControlDesk, MATLAB/Simulink), Python-based regression automation integrated with CI/CD.
- **Maruti Suzuki India Pvt. Ltd.** — Gurgaon, India — Assistant Manager, Automotive Systems
  Engineering — 08/2012–06/2015. Electric Power Steering and Airbag Controller ECU development,
  validation (HiL, bench, vehicle-level), requirements/specifications, root-cause analysis for
  field and quality issues.
- **Research Assistant** — Niedersächsisches Forschungszentrum Fahrzeugtechnik, TU Braunschweig —
  02/2012–07/2012 (minor entry, present only in the German-language source; MATLAB/Simulink
  quarter-car/full-car simulation for semi-active suspension control strategies, MiL simulation).

## 2. Canonical capabilities

- **Product & Agile delivery**: Product Ownership, Technical Product Ownership, SAFe, Agile
  Release Train (ART), PI Planning, backlog refinement, user stories, acceptance criteria,
  dependency management, stakeholder alignment.
- **Connected vehicle / automotive domain**: connected vehicle services, software-defined vehicle
  concepts, vehicle-to-cloud integration, Fleet Telematics, Remote Services, User Authorization,
  Feature Enrollment, EV Charging Schedules, Subscription Services.
- **Cloud & DevOps**: Google Cloud Platform (Cloud Functions, Cloud Run, BigQuery, Cloud Storage,
  GKE, IAM, Cloud Monitoring), Docker, Kubernetes, Terraform, CI/CD, Git/GitHub.
- **Data engineering & analytics**: Python, SQL, pandas, dbt, Airflow, data pipelines, data
  quality/validation/contracts, dashboards, KPI/SLA/SLO monitoring, BI reporting.
- **Systems engineering & validation**: ADAS, ECU validation, HiL/MiL/SiL testing, dSPACE
  AutomationDesk/ControlDesk, MATLAB/Simulink, CAN tooling (CANalyzer, CANoe, CANape), requirements
  engineering, traceability, root-cause analysis.
- **Compliance & governance awareness**: GDPR/DSGVO, ePrivacy, EU Data Act, ISO 26262 awareness,
  ISO 21434 awareness, ASPICE awareness, UNECE R155/R156 awareness.
- **AI/GenAI (practical/conceptual level only — see §6)**: RAG concepts, LLM awareness, prompt
  engineering awareness, AI-assisted engineering-automation concepts.
- **Languages**: English — full professional proficiency (consistent across all sources); German
  — see §7 (level stated inconsistently across sources); Tamil — native.

## 3. Achievements and metrics

Exact metrics as they appear in the source material, not invented or rounded further:

- Enabled monitoring for **over 1,000 fleet vehicles** using event-based metrics and cloud
  monitoring/dashboards. *(AC-MEMORY_PROFILE.md §6)*
- Containerized workers processing **~100 vehicle signals** and **~20 event types** for telemetry
  and alerting. *(AC-MEMORY_PROFILE.md §6)*
- Alerting logic validated for **14 Fleet-related events** before production enablement.
  *(AC-MEMORY_PROFILE.md §6)*
- Connected-vehicle data accuracy improved to **above 99%** through automated validation, dbt
  checks, data contracts, and monitoring. *(AC-MEMORY_PROFILE.md §6; corroborated repeatedly in
  both corpora)*
- KPI/SLA/SLO monitoring dashboards built across multiple roles/programs for service-quality
  transparency and issue triage. *(AC-MEMORY_PROFILE.md §6)*
- Modular, reusable HiL test architecture (Continental) avoided **approximately €80,000** in
  additional test-equipment investment. *(AC-MEMORY_PROFILE.md §6; corroborated repeatedly in both
  corpora)*
- Compliance-aware redesign of connected-vehicle data flows for GDPR/ePrivacy/EU Data Act.
  *(AC-MEMORY_PROFILE.md §6)*
- Practical GenAI/RAG concept work, including a RAG-chatbot concept for an automotive
  dealer/call-centre use case — a prototype/concept-level achievement, not a claimed large-scale
  production AI deployment. *(corroborated in both corpora; explicitly qualified as such in
  AC-profile_english.md's Key Achievements list)*

## 4. Certifications and education

- **Certifications** (from `AC-profile_german.md`'s Certifications section, corroborated in
  numerous application-specific sections of both corpora): Google Cloud Certified Associate Cloud
  Engineer; IBM Data Science Professional Certificate; Stanford Online Machine Learning
  Certificate.
- **Education** (from `AC-profile_german.md`'s Education section): M.Tech, Automotive Electronics
  & Embedded Systems — Vel Tech University, Chennai & ARAI, Pune, India (2010–2012). B.Tech,
  Electronics & Communication Engineering — National Institute of Technology, Srinagar, India
  (2005–2009).
- `AC-MEMORY_PROFILE.md` itself defers static profile fields (including certifications) to a
  `profile_static.yaml` file it references as authoritative — that file is not among the three
  bootstrap sources reviewed for this snapshot and was not available to synthesize from.

## 5. Languages

- English — full professional proficiency / fluent (consistent across all three sources).
- German — see §7; sources disagree on the exact current level.
- Tamil — native.

## 6. Experience-level limitations (constraint plane)

Directly from `AC-MEMORY_PROFILE.md` §9 ("Claims to Use Carefully") and §11 (confirmation-level
rules) — these are safe-wording constraints, not resume-eligible facts, and must never be used to
independently satisfy a job requirement match:

- **AI/GenAI**: describe as awareness/practical concepts (RAG, AI-assisted workflows, responsible-
  AI awareness) — do not claim "owned enterprise AI governance" or deep ML/LLM platform
  architecture ownership.
- **EU AI Act**: awareness only — do not claim an implemented compliance program.
- **AWS**: transferable cloud-architecture concepts from GCP — do not claim deep AWS production
  ownership unless separately evidenced.
- **Azure**: transferable architecture concepts only — do not claim hands-on Azure production
  delivery.
- **Spark/PySpark**: exposure only — do not claim "advanced Spark platform engineer" depth.
- **Formal architecture ownership**: describe as solution/integration architecture support — do
  not claim full formal Enterprise Architect ownership unless explicitly required and supported.
- **People/line management**: describe as senior technical leadership and stakeholder
  coordination — do not claim a formal people-manager/line-manager title.
- **Ford–Volkswagen alliance**: prefer "JV/alliance integration" framing over repeating specific
  partner company names in every context.
- **Formal on-call**: describe as incident triage / production issue resolution — do not claim
  formal on-call rotation ownership unless explicitly confirmed.
- **Go programming**: describe as "currently learning" — do not claim experienced Go backend
  engineering.

Per `AC-MEMORY_PROFILE.md` §11, any new experience claim not already supported by these sources
requires the operator to confirm its experience level (awareness / currently learning / prototype
exposure / professional delivery / production operation / architecture ownership / leadership)
before it is used — absent that confirmation, conservative wording applies.

## 7. Unresolved contradictions

These are preserved deliberately, not resolved, per this document's own scope — the operator must
resolve them (a future M3 conflict-resolution workflow will surface them as `MemoryConflict`
records):

1. **Employment/contracting structure and dates.** Most of the source material frames the last
   ~13 years as direct employment: Ford Motor Werk GmbH (Köln, 07/2017–Present), Continental
   Automotive (Nuremberg, 07/2015–07/2017), Maruti Suzuki (Gurgaon, 08/2012–06/2015). However, a
   section of `AC-profile_german.md` frames the same period as agency contracting through
   **"Ambigai Consultancy Services"** with different clients and materially different dates and
   even a different Continental location: client "Ford Connectivity" Aug 2021–Aug 2025; client
   "Ford Chassis Controls" Nov 2017–Dec 2020; client "Continental Automotive" Jul 2015–Oct 2017,
   Frankfurt am Main (not Nuremberg). This is a genuine, unresolved conflict about employment
   structure, employer-of-record, dates, and even work location — not a simple wording variation.
2. **German language proficiency level.** Stated variously across sources as "B1", "Professional
   Working Proficiency", "gute bis sehr gute berufliche Kommunikationsfähigkeit", "actively
   improving toward C1", and "B2 in Vorbereitung/aktiver Verbesserung" — with explicit instructions
   elsewhere not to overclaim C1 or native-level fluency. `AC-MEMORY_PROFILE.md` itself states
   "Professional Working Proficiency," but the two corpora disagree with each other and with it on
   the precise current CEFR level.
3. **Maruti Suzuki start date.** Stated as "08/2012" in the majority of sources but "Sep 2012" in
   at least one section adjacent to the Ambigai-framed material in `AC-profile_german.md`. Minor,
   but real, and should be confirmed rather than assumed.

## 8. Source manifest and precedence policy

Per `docs/DECISIONS.md` D-015, source precedence (highest to lowest) is:

1. `docs/AC/AC-MEMORY_PROFILE.md` — primary curated profile; highest-precedence source for
   limitations, safe wording, and profile policy.
2. `docs/AC/AC-profile_english.md` — operator-approved English evidence and expression corpus.
3. `docs/AC/AC-profile_german.md` — operator-approved German evidence and expression corpus.

| File | Lines | SHA-256 |
|---|---|---|
| `docs/AC/AC-MEMORY_PROFILE.md` | 268 | `5fd8674a772944d8c1354d8b280c0adb931411b17f1c755a52312baae3f2de28` |
| `docs/AC/AC-profile_english.md` | 2159 | `046537e6879926d4783843a0ebfbef481c7d959ac859748444d4cd41419be27a` |
| `docs/AC/AC-profile_german.md` | 1901 | `f0c6db9ba0cee18f806256feb7ef017449469be19f0f1cf668ed0b0ba69d2374` |

Hashes computed 2026-09-02 via `shasum -a 256` against the files as committed at that time; if the
files are ever edited, these hashes (and this snapshot) become stale and must be regenerated —
per D-015, the files are meant to be immutable bootstrap evidence, not living documents.

Note on scope: `AC-profile_english.md` and `AC-profile_german.md` are consolidated inputs from
roughly 40 previously tailored, job-specific resumes each — the large majority of their content is
positioning-plane material (alternative titles, alternative summaries, per-application tailoring
instructions) rather than canonical evidence. This snapshot deliberately does not enumerate that
material; §1–§6 above are the distilled, cross-corroborated canonical result.

This snapshot intentionally omits contact details (name, email, phone, location, professional
network links) present in the source files — this is a professional-memory reference, not a
contact-information export.
