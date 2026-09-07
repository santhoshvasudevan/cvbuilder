"""Agent Builder's structured LLM output contract (D-007/D-014/D-019, `docs/RESUME_OUTPUT_
STRUCTURE.md` Sec 2). Every model here uses `extra="forbid"`, and none of them has a field for
employer, client, role title, dates, location, or presentation mode -- D-019's static-profile
boundary means Agent Builder never receives or produces those; it only ever selects *which*
`CareerEngagement` a section is about (by `engagement_id`) and writes evidence-backed narrative
text. `BaseLLMAdapter.generate()` re-validates the provider's raw response against this schema
(D-005), so a provider that tried to smuggle a forbidden field through fails schema validation
before this application code ever sees the result -- not merely "gets ignored by convention".
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# D-037 completeness enforcement: a v1 bound on generated bullets per engagement, named and
# documented rather than left unbounded. Chosen generously relative to a typical resume's own
# experience-section length (3-6 bullets per role is a common convention) so it never constrains a
# genuinely well-supported engagement, while still giving `no_fabrication.py`'s post-response
# check (the authoritative enforcement -- this schema-level `max_length` is a best-effort signal
# only, since not every provider's structured-output mode is guaranteed to enforce list-length
# constraints) something concrete to reject runaway over-generation against. Enforced by rejecting
# the whole build (`NoFabricationError`), never by silently truncating which bullets are kept --
# truncation would be an arbitrary, non-deterministic choice among a model's own bullets.
MAX_BULLETS_PER_ENGAGEMENT = 6


class ResumeElementItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    supporting_memory_claim_ids: list[str] = Field(default_factory=list)
    matched_job_requirement_ids: list[str] = Field(default_factory=list)


class ExperienceSectionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engagement_id: str
    bullets: list[ResumeElementItem] = Field(default_factory=list, max_length=MAX_BULLETS_PER_ENGAGEMENT)


class SkillCategoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_name: str
    skills: list[ResumeElementItem] = Field(default_factory=list)


class TargetPositioningItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_options: list[str] = Field(default_factory=list)
    recommended_title: str


class PositioningGuidanceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_role_positioning: str = ""
    do_not_overstate: list[str] = Field(default_factory=list)
    terminology_preferences: list[str] = Field(default_factory=list)
    context_only_technologies: list[str] = Field(default_factory=list)
    naming_privacy_preferences: str = ""
    resume_language: str = "en"


class AgentBuilderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_positioning: TargetPositioningItem
    summary_elements: list[ResumeElementItem] = Field(default_factory=list)
    experience_sections: list[ExperienceSectionItem] = Field(default_factory=list)
    positioning_themes: list[ResumeElementItem] = Field(default_factory=list)
    achievements: list[ResumeElementItem] = Field(default_factory=list)
    skill_categories: list[SkillCategoryItem] = Field(default_factory=list)
    selected_skills: list[ResumeElementItem] = Field(default_factory=list)
    certifications: list[ResumeElementItem] = Field(default_factory=list)
    languages: list[ResumeElementItem] = Field(default_factory=list)
    positioning_guidance: PositioningGuidanceItem
