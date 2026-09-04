"""Deterministic integrity validation for Agent Jobber's structured output (2026-09-04 AJ
hardening, D-022; course-corrected 2026-09-04, D-023).

Product-owner boundary (D-023): "LLMs decide meaning and wording. Deterministic code protects
truth, boundaries and lifecycle. The operator approves semantic quality." The AJ/AC/AB LLMs are
responsible for recruiter-style interpretation -- MANDATORY vs. PREFERRED classification, what
counts as a screening risk, implied expectations, working in the posting's own language. The
operator is responsible, at Human Review Gate 1, for judging whether those classifications are
*correct*. This module must never grow a phrase list, a posting-length threshold, or any other
heuristic that tries to infer meaning or intent from wording -- that is exactly the D-022 overreach
D-023 corrected. It checks three purely objective properties only: an analysis produced at least
one requirement, no requirement duplicates another verbatim, and every requirement/screening risk
that claims posting support can actually be found, verbatim, in the posting.

`find_integrity_violations` is the one entry point: given the parsed `AgentJobberAnalysis` and the
exact posting text that was analyzed, returns a list of human-readable violation messages (empty =
valid). `services.intake.run_intake`/`rerun_analysis` call this before persistence begins and fail
closed on any violation -- never a partial `JobRequirement` set, never a pointer/pipeline advance.
"""

from __future__ import annotations

from ..schemas import AgentJobberAnalysis, RequirementCategory

# Categories whose content becomes a real, operator-relied-upon JobRequirement and therefore
# requires verifiable provenance. ATS_SIGNAL is often a bare keyword rather than a quotable
# sentence (schema-optional source_context), and IMPLIED_EXPECTATION is by definition not stated
# outright -- its source_context is grounding context, never a literal quote (schemas.py's own
# docstring) -- so neither is held to the exact-substring bar.
_PROVENANCE_REQUIRED_CATEGORIES = frozenset(
    {RequirementCategory.MANDATORY, RequirementCategory.PREFERRED, RequirementCategory.RESPONSIBILITY}
)


def _normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _has_provenance(source_context: str, posting_text: str) -> bool:
    """Exact-substring provenance check only -- mirrors `MemoryClaimSupport.
    verify_against_source`'s established convention (never a fuzzy/paraphrase match). This proves
    the quoted text exists in the posting; it says nothing about, and never judges, what that text
    *means* or whether the requirement/risk built from it is a reasonable interpretation -- that is
    the operator's call, not this function's."""
    quotation = source_context.strip()
    return bool(quotation) and quotation in posting_text


def find_integrity_violations(analysis: AgentJobberAnalysis, posting_text: str) -> list[str]:
    """Objective integrity checks only: at least one requirement exists, no requirement is an
    exact duplicate of another, and every requirement/screening-risk claiming posting support has
    verifiable, exact provenance. Deliberately does not, and must never, attempt to judge whether a
    posting is "substantive enough" to expect requirements, whether wording sounds like a
    candidate-gap judgment, or whether a screening risk was the right place for some piece of
    content -- those are semantic judgments reserved for the AJ LLM and the operator (D-023)."""
    violations: list[str] = []

    if not analysis.requirements:
        violations.append(
            "Zero requirements were extracted. A usable analysis must contain at least one "
            "JobRequirement, regardless of posting length -- this is classified as an incomplete "
            "analysis and will not become the current usable JobRequirementAnalysis."
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

        if requirement.category in _PROVENANCE_REQUIRED_CATEGORIES and not _has_provenance(
            requirement.source_context, posting_text
        ):
            violations.append(
                f"Requirement #{index} ({requirement.category.value}) {requirement.text!r} has no "
                "source_context that is an exact quotation from the posting -- unsupported "
                "requirement text."
            )

    for index, risk in enumerate(analysis.screening_risks, start=1):
        if not _has_provenance(risk.source_context, posting_text):
            violations.append(
                f"Screening risk #{index} {risk.text!r} has no source_context that is an exact "
                "quotation from the posting -- unsupported risk text."
            )

    return violations
