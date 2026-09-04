"""Deterministic semantic sanity validation for Agent Jobber's structured output (2026-09-04 AJ
hardening, D-022).

Schema validation (`schemas.AgentJobberAnalysis`) only proves the response is *shaped* correctly --
it cannot catch a schema-valid response that is substantively wrong, which is exactly what the
JobApplication id=9 live failure was: a well-formed `AgentJobberAnalysis` with `requirements=[]`
and fourteen `screening_risks` phrased as candidate-gap judgments ("Lack of hands-on experience
with...", "No proven ability to...") for a posting that plainly had extractable responsibilities
and qualifications.

`find_sanity_violations` is the one entry point: given the parsed `AgentJobberAnalysis` and the
exact posting text that was analyzed, it returns a list of human-readable violation messages (empty
= sane). `services.intake.run_intake`/`rerun_analysis` call this *before* persistence begins and
raise closed on any violation -- never a partial `JobRequirement` set, never a pointer advance.

Every check here is a deterministic, lexical rule -- never a fuzzy/semantic similarity test,
consistent with this codebase's established convention (`candidate_matching.services.dedup`,
`candidate_memory.models.MemoryClaimSupport.verify_against_source`).
"""

from __future__ import annotations

from ..schemas import AgentJobberAnalysis, RequirementCategory

# Below this length, a posting is treated as too short to reliably contain extractable
# responsibilities/qualifications -- zero requirements from genuinely thin/non-substantive input
# (e.g. a one-line stub) is not itself suspicious. Comfortably above `intake.MIN_PASTED_TEXT_CHARS`
# (20), which only guards against near-empty input, not against "too short to be a real posting".
SUBSTANTIVE_TEXT_MIN_CHARS = 300

# Categories whose content becomes a real, operator-relied-upon JobRequirement -- these require
# verifiable provenance. ATS_SIGNAL is often a bare keyword rather than a quotable sentence, and
# IMPLIED_EXPECTATION is by definition not stated outright (its source_context is grounding
# context, never a literal quote) -- neither is held to the same exact-substring bar.
_PROVENANCE_REQUIRED_CATEGORIES = frozenset(
    {RequirementCategory.MANDATORY, RequirementCategory.PREFERRED, RequirementCategory.RESPONSIBILITY}
)

# Deterministic, lexical markers of a candidate-gap judgment -- exactly the phrasing pattern the
# JobApplication id=9 failure used for all fourteen of its screening risks. Matched as a
# case-insensitive substring, never a semantic/fuzzy test. Agent Jobber has no candidate context
# (module docstring), so any of these phrases is definitionally an inference it cannot honestly
# make.
GAP_LANGUAGE_MARKERS: tuple[str, ...] = (
    "lack of",
    "lacking",
    "lacks",
    "no experience",
    "no proven",
    "no track record",
    "no demonstrated",
    "no background",
    "no evidence",
    "insufficient",
    "absence of",
    "failure to",
    "failed to",
    "missing experience",
    "unable to",
    "never built",
    "never delivered",
    "never demonstrated",
    "does not have",
    "doesn't have",
    "not experienced",
    "weak interpersonal",
    "weak communication",
    "weak at",
    "limited experience",
    "limited continuous-learning",
    "limited continuous learning",
)


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _has_gap_language(text: str) -> str | None:
    lowered = text.lower()
    for marker in GAP_LANGUAGE_MARKERS:
        if marker in lowered:
            return marker
    return None


def _has_provenance(source_context: str, posting_text: str) -> bool:
    """Exact-substring provenance, mirroring `MemoryClaimSupport.verify_against_source`'s
    convention -- never a fuzzy/paraphrase match."""
    quotation = source_context.strip()
    return bool(quotation) and quotation in posting_text


def find_sanity_violations(analysis: AgentJobberAnalysis, posting_text: str) -> list[str]:
    violations: list[str] = []

    is_substantive = len(posting_text.strip()) >= SUBSTANTIVE_TEXT_MIN_CHARS

    if not analysis.requirements:
        if is_substantive:
            violations.append(
                "Zero requirements were extracted from a substantive posting "
                f"({len(posting_text.strip())} characters) -- a real posting's responsibilities/ "
                "qualifications/skills must become requirements, not be summarized away."
            )
        if analysis.screening_risks:
            violations.append(
                f"{len(analysis.screening_risks)} screening risk(s) were produced but zero "
                "requirements were extracted -- this is the exact failure shape of responsibilities/"
                "qualifications being misclassified as screening risks instead of requirements."
            )

    seen_requirement_keys: dict[tuple[str, str], int] = {}
    for index, requirement in enumerate(analysis.requirements, start=1):
        key = (requirement.category.value, _normalize(requirement.text))
        if key in seen_requirement_keys:
            violations.append(
                f"Requirement #{index} ({requirement.category.value}) duplicates requirement "
                f"#{seen_requirement_keys[key]} -- same category and text extracted twice from the "
                "same posting."
            )
        else:
            seen_requirement_keys[key] = index

        gap_marker = _has_gap_language(requirement.text)
        if gap_marker:
            violations.append(
                f"Requirement #{index} text {requirement.text!r} contains candidate-judgment "
                f"language ({gap_marker!r}) -- Agent Jobber has no candidate context and must never "
                "phrase posting content as a claim about a candidate's gap."
            )

        if requirement.category in _PROVENANCE_REQUIRED_CATEGORIES and not _has_provenance(
            requirement.source_context, posting_text
        ):
            violations.append(
                f"Requirement #{index} ({requirement.category.value}) {requirement.text!r} has no "
                "source_context that is an exact quotation from the posting -- unsupported "
                "requirement text."
            )

    for index, risk in enumerate(analysis.screening_risks, start=1):
        gap_marker = _has_gap_language(risk.text)
        if gap_marker:
            violations.append(
                f"Screening risk #{index} {risk.text!r} contains candidate-judgment language "
                f"({gap_marker!r}) -- a screening risk must be an explicit hiring constraint the "
                "posting states, never a restatement of a responsibility as a candidate's gap."
            )
        if not _has_provenance(risk.source_context, posting_text):
            violations.append(
                f"Screening risk #{index} {risk.text!r} has no source_context that is an exact "
                "quotation from the posting -- unsupported risk text."
            )

    return violations
