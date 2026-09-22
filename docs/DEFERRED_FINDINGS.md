# Deferred Findings

This append-only register preserves findings deferred from bounded development runs. Existing entries must not be edited or removed; later disposition changes must be appended as new records.

## AUDIT-002

- Source run ID: `m3a-d2c0cc48b6aa`
- Source artifact: `.orchestration/runs/m3a-d2c0cc48b6aa/handoffs/audit-02.json`
- Candidate SHA: `969283968a6772cc1b9f7930241a8a0a33c7e857`
- Severity: `HIGH`
- Finding status: `OPEN`
- Disposition: `DEFERRED`
- Owner: unassigned

Verbatim source finding:

```json
{
  "decision_ids": [
    "V2-D003",
    "V2-D030"
  ],
  "explanation": "Claim provenance is described as immutable but is not enforced. MemorySourceDocument and MemoryClaimSupport permit ordinary ORM updates/deletes; the registered source-document admin also permits deletion. Deleting a source document cascades its claim-support rows, destroying the evidence trail.",
  "finding_id": "AUDIT-002",
  "location": "candidate_memory/models.py:25; candidate_memory/models.py:105; candidate_memory/admin.py:46",
  "required_correction": "Make source documents and claim-support provenance append-only/non-deletable except through an explicitly designed whole-revision lifecycle, and add regression tests proving content, hash, source path, and support links cannot be modified or removed individually.",
  "requirement_ids": [
    "FACT-002"
  ],
  "severity": "HIGH",
  "status": "OPEN",
  "test_added": false,
  "test_commit_sha": ""
}
```

## CLAUDE-M3A-002

- Source run ID: `m3a-d2c0cc48b6aa`
- Source artifact: `.orchestration/runs/m3a-d2c0cc48b6aa/supplemental-audits/claude-m3a-independent-audit-01.json`
- Candidate SHA: `969283968a6772cc1b9f7930241a8a0a33c7e857`
- Severity: `MEDIUM`
- Finding status: `OPEN`
- Disposition: `DEFERRED`
- Owner: unassigned

Verbatim source finding:

```json
{
  "affected_paths": [
    "candidate_memory/services/ingestion.py",
    "candidate_memory/tests/test_ingestion.py"
  ],
  "decision_ids": [
    "V2-D003"
  ],
  "evidence": "There is no conflict-detection logic for the Maruti start-date contradiction. Claude's live reproduction found zero conflicts even though ten ingested claims literally contain 08/2012 or Sep 2012.",
  "id": "CLAUDE-M3A-002",
  "reproduction_commands": [
    "manage.py shell: ingest_default_candidate_sources(memory), then inspect MemoryConflict topics; only two conflicts exist and none is the Maruti start date."
  ],
  "required_corrections": [
    "Detect and retain the Maruti start-date contradiction as an unresolved MemoryConflict.",
    "Add a regression covering all three known source contradictions.",
    "Restore this durable reviewer-003 finding to the active correction scope."
  ],
  "requirement_ids": [
    "FACT-002",
    "requirements.md §6.1"
  ],
  "severity": "MEDIUM"
}
```

## AUDIT-002 — RESOLVED

- Disposition record run ID: `m3a-c1-6df5432d618c`
- Candidate SHA: `50847ab53fb859d2a690d307706350477be0a996`
- Finding status: `RESOLVED`
- Disposition: `RESOLVED`
- Resolved by: M3A-C1 corrective implementation on the integrated M3A baseline

Append-only disposition: original `AUDIT-002` OPEN/DEFERRED entry above is unchanged. Closure evidence: `MemorySourceDocument` and `MemoryClaimSupport` reject post-creation instance and QuerySet update/delete via `HardIntegrityError`/`IntegrityFinding(code=PROVENANCE_IMMUTABLE)`; source-document admin `has_delete_permission` is false; claim-support `source_document` FK uses `PROTECT` (`candidate_memory.0002_protect_claim_support_source`); regression `candidate_memory.tests.test_ac1_source_provenance_immutability`.

## CLAUDE-M3A-002 — RESOLVED

- Disposition record run ID: `m3a-c1-6df5432d618c`
- Candidate SHA: `50847ab53fb859d2a690d307706350477be0a996`
- Finding status: `RESOLVED`
- Disposition: `RESOLVED`
- Resolved by: M3A-C1 corrective implementation on the integrated M3A baseline

Append-only disposition: original `CLAUDE-M3A-002` OPEN/DEFERRED entry above is unchanged. Closure evidence: generic same-engagement start/end date contradiction detection retains unresolved `MemoryConflict` rows; known Maruti start-date contradiction retained as `engagement_start_date:maruti_suzuki`; regression `candidate_memory.tests.test_ac2_engagement_date_contradictions`. Non-date contradiction categories remain out of scope/deferred.
