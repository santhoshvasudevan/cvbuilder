"""Agent Builder's LLM-backed structured content generation, routed only through the M2
`llm_provider` adapter interface (`get_adapter_for_stage`) -- no provider SDK import anywhere in
this app, mirroring `job_intake.services.analyze`/`candidate_matching.services.assess`.

Per D-019, the model is given engagement summaries (id, title, organisation, dates, location --
read-only context to cite by ID) but its own output schema (`AgentBuilderOutput`) has no field for
any of those static values; it may only select an `engagement_id` and write evidence-backed
narrative bullets.
"""

from __future__ import annotations

import json

from candidate_matching.services.retrieval_limits import (
    MAX_ESTIMATED_REQUEST_TOKENS,
    RetrievalBudgetExceededError,
    estimate_tokens,
)
from candidate_matching.services.retrieve import (
    RETRIEVAL_REASON_ENGAGEMENT_ANCHOR,
    RETRIEVAL_REASON_JOB_RELEVANT,
    RETRIEVAL_REASON_LANGUAGE_EVIDENCE,
    RetrievalContext,
    RetrievedClaim,
)
from llm_provider.adapters import get_adapter_for_stage
from llm_provider.models import StageModelAssignment
from llm_provider.types import NormalizedLLMRequest, NormalizedLLMResult

from ..schemas import AgentBuilderOutput

DEFAULT_MAX_OUTPUT_TOKENS = 4096

SYSTEM_PROMPT = """\
You are Agent Builder: you write a truthful, tailored resume for one specific job, from one
candidate's verified background.

You are given:
- the target job's employer, role title, and its requirements, each with the disposition Agent
  Candidate already assessed (MATCH/PARTIAL/GAP/UNKNOWN) and, where relevant, an explanation;
- a "Baseline career chronology": every one of the candidate's approved career engagements (each
  with an engagement_id, a role title, an organisation, and dates), unconditionally, whether or not
  this specific job posting's requirements happen to relate to it. This is read-only context for
  you to select among and cite by engagement_id: you must NEVER restate, rephrase, or invent an
  employer name, job title, location, or date anywhere in your output -- your output schema has no
  field for any of those, by design, and any attempt to add one will be rejected outright. An
  engagement marked "NO ELIGIBLE NARRATIVE EVIDENCE CURRENTLY AVAILABLE" has no confirmed evidence
  to write a bullet from right now -- do not invent one; it is entirely correct to write zero
  bullets for that engagement (its header will still be shown, from this same chronology, exactly
  as it is here) rather than fabricate content to fill it;
- "Job-relevant evidence": the candidate's confirmed, resume-eligible narrative claims Agent
  Candidate's ranking selected as relevant to this specific posting's requirements;
- "Engagement anchor evidence": a small, fixed set of each engagement's own confirmed,
  resume-eligible narrative claims, included independently of this posting's specific requirements
  so every approved engagement has some substantive grounding available even when this posting
  never happens to phrase a requirement that scores that engagement's evidence highly;
- "Confirmed language evidence": the candidate's confirmed, resume-eligible language-proficiency
  claims, always included regardless of this posting's requirements;
- a bounded set of candidate rules (cautions/preferences/learning-status) that must inform your
  wording (e.g. a skill marked "still learning" must never be presented as production-grade
  expertise) but which are never themselves resume evidence.

A claim may appear in more than one of the evidence lists above (e.g. a claim that is both this
posting's job-relevant evidence and one of its engagement's anchor claims) -- it is still exactly
one claim, cited by exactly one claim_id.

Select the strongest truthful positioning: emphasize what genuinely matches, and do not invent,
exaggerate, or imply experience beyond what the given claims support. Every factual statement you
write (summary line, experience bullet, positioning theme, achievement, skill, certification,
language entry) MUST cite at least one real claim_id from the context you were given -- never
invent an ID; a fabricated ID will be rejected before anything is rendered, so there is no benefit
to guessing. Group narrative bullets under the engagement_id they actually belong to; use only
engagement_id values that appear in the Baseline career chronology above. Do not force a bullet for
an engagement merely to fill space -- write only what the given evidence actually supports.
"""


def _claim_line(claim: RetrievedClaim) -> str:
    engagement_note = (
        f" [engagements: {', '.join(claim.approved_engagement_ids)}]"
        if claim.approved_engagement_ids
        else " [global]"
    )
    return f"- ({claim.claim_id}) [{claim.claim_type}]{engagement_note} {claim.text}"


def build_request(
    jra,
    requirement_assessments: list,
    retrieval: RetrievalContext,
    *,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> NormalizedLLMRequest:
    lines = [f"Target job: {jra.role_title or '(unstated role)'} at {jra.employer or '(unstated employer)'}"]

    lines.append("\nRequirement assessment (from Agent Candidate):")
    for assessment in requirement_assessments:
        lines.append(f"- ({assessment.requirement_id}) {assessment.disposition}: {assessment.explanation}")

    no_evidence_engagements = set(retrieval.engagements_without_eligible_evidence)
    lines.append(
        "\nBaseline career chronology (every approved engagement -- always present, independent of "
        "this job posting's requirements):"
    )
    for engagement in retrieval.engagements:
        status = "current" if engagement.is_current else "past"
        diagnostic = (
            " [NO ELIGIBLE NARRATIVE EVIDENCE CURRENTLY AVAILABLE -- do not invent bullets for this "
            "engagement]"
            if engagement.engagement_id in no_evidence_engagements
            else ""
        )
        lines.append(
            f"- ({engagement.engagement_id}) {engagement.approved_role_title} at "
            f"{engagement.displayed_organization}, {status}{diagnostic}"
        )

    job_relevant = [c for c in retrieval.claims if RETRIEVAL_REASON_JOB_RELEVANT in c.retrieval_reasons]
    anchors = [c for c in retrieval.claims if RETRIEVAL_REASON_ENGAGEMENT_ANCHOR in c.retrieval_reasons]
    language = [c for c in retrieval.claims if RETRIEVAL_REASON_LANGUAGE_EVIDENCE in c.retrieval_reasons]
    untagged = [c for c in retrieval.claims if not c.retrieval_reasons]

    if job_relevant:
        lines.append("\nJob-relevant evidence (selected for this posting's specific requirements):")
        lines.extend(_claim_line(c) for c in job_relevant)
    if anchors:
        lines.append(
            "\nEngagement anchor evidence (always included per engagement, independent of this "
            "posting's requirements):"
        )
        lines.extend(_claim_line(c) for c in anchors)
    if language:
        lines.append("\nConfirmed language evidence (always included):")
        lines.extend(_claim_line(c) for c in language)
    if untagged:
        # Defensive only -- every claim `services/context.py` places in `retrieval.claims` is
        # tagged with at least one reason; this never fires in the real pipeline, but a
        # hand-built RetrievalContext (e.g. in a test) is never silently dropped from the prompt.
        lines.append("\nOther candidate evidence:")
        lines.extend(_claim_line(c) for c in untagged)

    if retrieval.rules:
        lines.append("\nCandidate rules (constraints -- never resume evidence):")
        for rule in retrieval.rules:
            lines.append(f"- [{rule.rule_type}] {rule.text}")

    return NormalizedLLMRequest(
        stage=StageModelAssignment.Stage.AB_BUILD,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(lines)},
        ],
        output_schema=AgentBuilderOutput,
        temperature=0.0,
        max_output_tokens=max_output_tokens,
    )


def _estimate_full_request_tokens(request: NormalizedLLMRequest) -> int:
    """D-037 Phase F: the authoritative request-size estimate, counting the *complete* assembled
    request -- every message (system instructions, JRA role/employer, requirement assessments and
    their explanations, all four evidence categories, engagement chronology lines including the
    NO_ELIGIBLE_EVIDENCE diagnostic text, and CandidateRules -- everything `build_request` above
    put into `request.messages`) plus the structured-output schema itself, which the provider also
    counts against its context window. Uses the project's one canonical estimator
    (`candidate_matching.services.retrieval_limits.estimate_tokens`) rather than a different
    tokenizer, so this figure is directly comparable to every other stage's own budget check."""
    message_text = "".join(message.get("content", "") for message in request.messages)
    schema_text = json.dumps(request.output_schema.model_json_schema(), sort_keys=True)
    return estimate_tokens(message_text) + estimate_tokens(schema_text)


def generate_resume_content(
    jra, requirement_assessments: list, retrieval: RetrievalContext
) -> NormalizedLLMResult:
    adapter = get_adapter_for_stage(StageModelAssignment.Stage.AB_BUILD)
    request = build_request(
        jra,
        requirement_assessments,
        retrieval,
        max_output_tokens=adapter.effective_max_output_tokens,
    )

    # D-037 Phase F: run after the final request is fully assembled, before the adapter performs
    # any HTTP call (`adapter.generate` below is what actually calls the provider). Fails closed,
    # sanitized -- the message reports only token counts and the configured bound, never any
    # request content -- and never truncates or drops evidence to force the request under budget.
    estimated_tokens = _estimate_full_request_tokens(request)
    if estimated_tokens > MAX_ESTIMATED_REQUEST_TOKENS:
        raise RetrievalBudgetExceededError(
            f"Estimated Agent Builder request size ({estimated_tokens} tokens, computed from the "
            "complete assembled request: system and user messages -- including JRA "
            "role/employer, every requirement's disposition and explanation, job-relevant "
            "evidence, engagement-anchor evidence, confirmed language evidence, baseline "
            "engagement chronology metadata, and candidate rules -- plus the structured-output "
            f"schema) exceeds MAX_ESTIMATED_REQUEST_TOKENS={MAX_ESTIMATED_REQUEST_TOKENS}. "
            "Refusing to call the provider; no evidence is ever dropped to force a request under "
            "budget -- lower MAX_ANCHOR_CLAIMS_PER_ENGAGEMENT/MAX_SELECTED_CLAIMS/MAX_RULES or "
            "raise the token budget deliberately."
        )

    return adapter.generate(request)
