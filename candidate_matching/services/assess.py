"""Agent Candidate's LLM-backed semantic assessment (M5 point 4), routed only through the M2
`llm_provider` adapter interface (`get_adapter_for_stage`) -- no provider SDK import anywhere in
this app, mirroring `job_intake.services.analyze`/`candidate_memory.services.extraction`.

Only called for job requirements that `services/static_requirements.classify()` did not resolve
locally (responsibilities, achievements, technologies, transferable-experience/narrative fit
questions) -- tenure/dates/current-past-status/location/employer-relationship requirements are
answered by `services/static_requirements.py` without any LLM call, per D-019.
"""

from __future__ import annotations

from llm_provider.adapters import get_adapter_for_stage
from llm_provider.models import StageModelAssignment
from llm_provider.types import NormalizedLLMRequest, NormalizedLLMResult

from ..schemas import AgentCandidateAssessment
from .retrieve import RetrievalContext

DEFAULT_MAX_OUTPUT_TOKENS = 4096

SYSTEM_PROMPT = """\
You are Agent Candidate: an experienced recruiter-side reviewer judging how well one candidate's
verified background fits one specific job's requirements.

You are given:
- a bounded set of the candidate's own confirmed, resume-eligible narrative claims (evidence of
  responsibilities, achievements, projects, and skills actually delivered) -- each with a claim_id;
- a bounded set of the candidate's approved career engagements (employer/role summaries) -- each
  with an engagement_id, for you to cite as context only, never to restate or alter;
- a bounded set of candidate rules (cautions/preferences/learning-status) that must inform your
  judgment (e.g. a skill listed as "still learning" must never be treated as production-grade
  experience) but which are never themselves resume evidence and can never alone support a MATCH;
- a list of job requirements that still need judgment (requirements resolvable by structural facts
  alone -- tenure, dates, location, current/past employment status -- have already been assessed
  deterministically and are not sent to you).

For every requirement given, produce exactly one assessment with a disposition of MATCH, PARTIAL,
GAP, or UNKNOWN:
- MATCH or PARTIAL requires citing at least one real claim_id and/or engagement_id from the context
  you were given -- never invent an ID, and never claim a match with no cited evidence.
- If the evidence does not support a requirement, say so honestly as GAP -- never soften or conceal
  a genuine mismatch to make the fit look better (no-concealment is mandatory, not optional).
- If you cannot judge a requirement from the given context at all, use UNKNOWN rather than guessing
  either way.
- Absence of evidence must never silently become MATCH.

Only cite claim_id/engagement_id values that literally appear in the context given to you below --
any ID you invent will be rejected by a downstream validator, so there is no benefit to guessing.
"""


def build_request(
    retrieval: RetrievalContext,
    requirements: list[dict],
    *,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> NormalizedLLMRequest:
    context_lines = ["Candidate narrative claims:"]
    for claim in retrieval.claims:
        engagement_note = (
            f" [engagements: {', '.join(claim.approved_engagement_ids)}]"
            if claim.approved_engagement_ids
            else " [global]"
        )
        context_lines.append(f"- ({claim.claim_id}) [{claim.claim_type}]{engagement_note} {claim.text}")

    context_lines.append("\nApproved career engagements:")
    for engagement in retrieval.engagements:
        status = "current" if engagement.is_current else "past"
        context_lines.append(
            f"- ({engagement.engagement_id}) {engagement.approved_role_title} at "
            f"{engagement.displayed_organization}, {status}"
        )

    if retrieval.rules:
        context_lines.append("\nCandidate rules (constraints -- never resume evidence):")
        for rule in retrieval.rules:
            context_lines.append(f"- [{rule.rule_type}] {rule.text}")

    context_lines.append("\nJob requirements to assess:")
    for requirement in requirements:
        context_lines.append(
            f"- ({requirement['requirement_id']}) [{requirement['category']}] {requirement['text']}"
        )

    return NormalizedLLMRequest(
        stage=StageModelAssignment.Stage.AC_MATCH,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(context_lines)},
        ],
        output_schema=AgentCandidateAssessment,
        temperature=0.0,
        max_output_tokens=max_output_tokens,
    )


def assess_requirements(
    retrieval: RetrievalContext, requirements: list[dict], *, requested_model_id: int | None = None
) -> NormalizedLLMResult:
    """`requested_model_id`, when given, is a per-run operator override for this one call
    (2026-09-07, per-run model selection) -- never persisted as a new stage default."""
    if not requirements:
        return NormalizedLLMResult(content=AgentCandidateAssessment(requirement_assessments=[]))
    adapter = get_adapter_for_stage(
        StageModelAssignment.Stage.AC_MATCH, requested_model_id=requested_model_id
    )
    request = build_request(
        retrieval, requirements, max_output_tokens=adapter.effective_max_output_tokens
    )
    return adapter.generate(request)
