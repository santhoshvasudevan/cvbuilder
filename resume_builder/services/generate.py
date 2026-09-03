"""Agent Builder's LLM-backed structured content generation, routed only through the M2
`llm_provider` adapter interface (`get_adapter_for_stage`) -- no provider SDK import anywhere in
this app, mirroring `job_intake.services.analyze`/`candidate_matching.services.assess`.

Per D-019, the model is given engagement summaries (id, title, organisation, dates, location --
read-only context to cite by ID) but its own output schema (`AgentBuilderOutput`) has no field for
any of those static values; it may only select an `engagement_id` and write evidence-backed
narrative bullets.
"""

from __future__ import annotations

from candidate_matching.services.retrieve import RetrievalContext
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
- a bounded set of the candidate's own confirmed, resume-eligible narrative claims -- each with a
  claim_id, and, where applicable, the engagement_id it belongs to;
- a bounded set of the candidate's approved career engagements -- each with an engagement_id, a
  role title, an organisation, and dates. This is read-only context for you to select among and
  cite by engagement_id: you must NEVER restate, rephrase, or invent an employer name, job title,
  location, or date anywhere in your output -- your output schema has no field for any of those,
  by design, and any attempt to add one will be rejected outright.
- a bounded set of candidate rules (cautions/preferences/learning-status) that must inform your
  wording (e.g. a skill marked "still learning" must never be presented as production-grade
  expertise) but which are never themselves resume evidence.

Select the strongest truthful positioning: emphasize what genuinely matches, and do not invent,
exaggerate, or imply experience beyond what the given claims support. Every factual statement you
write (summary line, experience bullet, positioning theme, achievement, skill, certification,
language entry) MUST cite at least one real claim_id from the context you were given -- never
invent an ID; a fabricated ID will be rejected before anything is rendered, so there is no benefit
to guessing. Group narrative bullets under the engagement_id they actually belong to; use only
engagement_id values that appear in the context above.
"""


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

    lines.append("\nCandidate narrative claims:")
    for claim in retrieval.claims:
        engagement_note = f" [engagement: {claim.engagement_id}]" if claim.engagement_id else " [global]"
        lines.append(f"- ({claim.claim_id}) [{claim.claim_type}]{engagement_note} {claim.text}")

    lines.append("\nApproved career engagements:")
    for engagement in retrieval.engagements:
        status = "current" if engagement.is_current else "past"
        lines.append(
            f"- ({engagement.engagement_id}) {engagement.approved_role_title} at "
            f"{engagement.displayed_organization}, {status}"
        )

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


def generate_resume_content(
    jra, requirement_assessments: list, retrieval: RetrievalContext
) -> NormalizedLLMResult:
    adapter = get_adapter_for_stage(StageModelAssignment.Stage.AB_BUILD)
    llm_model = adapter.llm_model
    max_output_tokens = llm_model.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS
    request = build_request(jra, requirement_assessments, retrieval, max_output_tokens=max_output_tokens)
    return adapter.generate(request)
