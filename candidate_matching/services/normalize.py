"""AC_NORMALIZE: a bounded, provider-backed requirement-normalization stage (2026-09-04 recall
repair, D-015/D-020), inserted *before* deterministic BM25 candidate generation:

    JobRequirement -> bounded canonical-English search representation -> BM25 candidate
    generation -> AC_RANK -> AC_MATCH

Pure lexical retrieval (`services/lexical_relevance.py`) cannot bridge a genuine vocabulary
mismatch -- a paraphrase that shares no words with the requirement, or a requirement written in a
language other than the candidate's stored (English) evidence. A 2026-09-04 independent re-audit
measured exactly this gap (a German-language requirement retrieving less evidence than its English
equivalent, and several English paraphrases retrieving none). This stage produces a small,
schema-bounded set of retrieval *hints* -- a canonical English restatement of the requirement's
meaning, plus bounded diagnostic terms/equivalents/preserved technical terms -- that
`candidate_generation.py` scores *in addition to* the requirement's own original text, never in
place of it.

Hard boundary (never weaken): this stage receives only requirement IDs, requirement text, and the
job posting's source language -- see `build_request` below. It must never receive a MemoryClaim, a
CareerEngagement, a candidate name/contact detail, an employer, a title, a location, or an
employment date. It cannot see the candidate's history, so it cannot fabricate evidence about it;
its output is retrieval hints only and is never persisted as a `MemoryClaim`, never rendered into
a resume, and never treated as evidence by any validator in this codebase.

Fails closed like every other stage in this pipeline (`RankingFailedError` is `bounded_retrieval.
py`'s precedent): a provider error, a schema-invalid/truncated/oversized response (see
`normalization_limits.py` and `schemas.RequirementNormalizationItem`), or a response that adds,
drops, or renames a requirement ID all raise `NormalizationFailedError` here -- never silently
falls back to unexpanded retrieval, which would be exactly the "silently continue with known-
degraded recall" this stage exists to prevent.
"""

from __future__ import annotations

from llm_provider.adapters import get_adapter_for_stage
from llm_provider.models import StageModelAssignment
from llm_provider.types import NormalizedLLMRequest, NormalizedLLMResult

from ..schemas import RequirementNormalizationItem, RequirementNormalizationOutput
from .dedup import normalize_text
from .normalization_limits import MAX_TERM_CHARS

DEFAULT_MAX_OUTPUT_TOKENS = 4096

SYSTEM_PROMPT = f"""\
You are a bounded requirement-normalization step in a resume-matching pipeline. You are given a \
job posting's source language and a list of job requirements (each with a requirement_id and its \
original text). You know nothing else about this job or any candidate -- do not assume or invent \
any fact about a candidate's history, employer, title, or evidence.

For EACH requirement, in the exact set of requirement_ids given (never add, drop, or rename an \
id), return:
- canonical_english_text: a short, faithful restatement of the requirement's meaning in English \
(translate if the source language is not English; otherwise normalize/paraphrase only).
- diagnostic_terms: a short list of the specific, diagnostic English search terms a matching \
resume claim might use (technologies, standards, products, domain-specific expressions) -- not \
generic words.
- equivalents: a short list of English synonyms or equivalent phrasings for the requirement's \
core concept.
- preserved_technical_terms: the exact acronyms, technology names, product names, or standards \
from the original requirement, preserved verbatim (never translated or altered).
- source_language: the job posting's source language you were given.

diagnostic_terms, equivalents, and preserved_technical_terms are always present in your output, \
one per requirement -- when a requirement genuinely has nothing to report for one of them, return \
an empty list for it rather than omitting the field or inventing content to fill it.

Every list is short and bounded -- do not enumerate exhaustively. Each entry in diagnostic_terms, \
equivalents, and preserved_technical_terms must be a short term or short phrase of at most \
{MAX_TERM_CHARS} characters -- never a complete sentence, an action clause, or a restatement of \
the full requirement. Never invent a specific technology, product, or standard that is not \
implied by the requirement text itself. This output is a retrieval hint only, never evidence, \
never a claim about any candidate.
"""


class NormalizationFailedError(Exception):
    """The AC_NORMALIZE stage failed (provider error), returned schema-invalid/oversized output,
    or returned a requirement_id set that does not exactly match the input -- raised instead of
    ever falling back to unexpanded (degraded-recall) retrieval."""


def build_request(
    requirements: list[dict],
    *,
    posting_language: str,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    reasoning_effort: str | None = None,
    correlation_id: str | None = None,
) -> NormalizedLLMRequest:
    """`requirements` items must supply only `requirement_id`/`text` -- callers (`bounded_
    retrieval.py`) are responsible for never passing a dict with any other key (category,
    CandidateMemory content, engagement/employment data) through to this stage."""
    lines = [f"Job posting source language: {posting_language}", "\nJob requirements:"]
    for requirement in requirements:
        lines.append(f"- ({requirement['requirement_id']}) {requirement['text']}")

    return NormalizedLLMRequest(
        stage=StageModelAssignment.Stage.AC_NORMALIZE,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(lines)},
        ],
        output_schema=RequirementNormalizationOutput,
        temperature=0.0,
        max_output_tokens=max_output_tokens,
        reasoning_effort=reasoning_effort,
        correlation_id=correlation_id,
    )


def expand_requirements_for_search(
    requirements: list[dict],
    *,
    posting_language: str,
    requested_model_id: int | None = None,
    requested_reasoning_effort: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, RequirementNormalizationItem]:
    """The orchestration entry point `bounded_retrieval.py` calls. Returns one
    `RequirementNormalizationItem` per input requirement, keyed by `requirement_id`. Raises
    `NormalizationFailedError` rather than ever returning a partial or degraded result.

    `requested_model_id`/`requested_reasoning_effort`, when given, are per-run operator
    overrides for this one call (2026-09-07, per-run model selection; paid GPT-5.4 model
    defaults) -- never persisted as a new stage default."""
    if not requirements:
        return {}

    input_ids = [requirement["requirement_id"] for requirement in requirements]

    adapter = get_adapter_for_stage(
        StageModelAssignment.Stage.AC_NORMALIZE,
        requested_model_id=requested_model_id,
        requested_reasoning_effort=requested_reasoning_effort,
    )
    request = build_request(
        requirements,
        posting_language=posting_language,
        max_output_tokens=adapter.effective_max_output_tokens,
        reasoning_effort=adapter.effective_reasoning_effort,
        correlation_id=correlation_id,
    )
    result: NormalizedLLMResult = adapter.generate(request)

    if result.is_error:
        raise NormalizationFailedError(
            f"AC_NORMALIZE requirement-normalization stage failed: {result.error.message}"
        )
    output = result.content
    if output is None or not hasattr(output, "items"):
        raise NormalizationFailedError("AC_NORMALIZE stage returned unusable output.")

    by_id = {item.requirement_id: item for item in output.items}
    returned_ids = set(by_id)
    expected_ids = set(input_ids)
    if returned_ids != expected_ids:
        missing = expected_ids - returned_ids
        added = returned_ids - expected_ids
        raise NormalizationFailedError(
            "AC_NORMALIZE stage returned a requirement_id set that does not match the input "
            f"(missing={sorted(missing)}, unexpected={sorted(added)}) -- failing closed rather "
            "than silently continuing with unexpanded or mismatched retrieval."
        )
    if len(by_id) != len(output.items):
        raise NormalizationFailedError(
            "AC_NORMALIZE stage returned a duplicate requirement_id -- failing closed."
        )
    return by_id


def build_search_text(original_text: str, normalization: RequirementNormalizationItem) -> str:
    """The union text `candidate_generation.py` scores against for one requirement: the original
    requirement text, its canonical English restatement, and every bounded term/equivalent/
    preserved technical term -- never used for anything but this in-memory scoring input; never
    stored as evidence, a claim, or resume content.

    An already-English requirement's canonical restatement is often word-for-word (or near
    word-for-word) identical to its original text -- repeating it verbatim adds no new
    vocabulary, only a second, differently-punctuated copy of the same sentence. Found during the
    2026-09-04 requirement-normalization work: concatenating that near-duplicate still shifts
    `lexical_relevance.py`'s exact-phrase bonus (new sentence-boundary bigrams/trigrams appear
    purely from the concatenation point, matching *other*, unrelated claims by coincidence) enough
    to push a borderline claim out of the bounded per-requirement cap -- a real, unwanted side
    effect of a supposedly-redundant restatement. `normalize_text` (the same normalized-equality
    check `dedup.py` uses to decide two claims are "the same content") is reused here to skip
    adding the restatement when it is not meaningfully different from the original -- never a
    fuzzy/semantic similarity test, an exact check after whitespace/case normalization only."""
    canonical = normalization.canonical_english_text
    parts = [original_text]
    if normalize_text(canonical) != normalize_text(original_text):
        parts.append(canonical)
    parts.extend(normalization.diagnostic_terms)
    parts.extend(normalization.equivalents)
    parts.extend(normalization.preserved_technical_terms)
    return " ".join(part for part in parts if part)
