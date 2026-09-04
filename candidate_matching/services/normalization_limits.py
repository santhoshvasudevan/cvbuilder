"""Hard bounds on the AC_NORMALIZE requirement-normalization stage's output (2026-09-04 recall
repair, D-015/D-020). Enforced as Pydantic field constraints on `schemas.RequirementNormalizationItem`
/`RequirementNormalizationOutput`, so an over-large response is a schema-validation failure --
handled uniformly by `llm_provider.adapters.base.BaseLLMAdapter.generate` exactly like any other
malformed provider response -- never a value silently truncated by application code after the
fact. Raising a limit here is never an acceptable *primary* fix for a recall gap; these exist to
keep the stage's output small and inspectable, not to tune recall.
"""

from __future__ import annotations

# One JobRequirementAnalysis rarely has more distinct requirements than this; a response
# containing more is treated as malformed rather than silently accepted.
MAX_NORMALIZATION_ITEMS = 100

# The canonical-English restatement of one requirement's meaning -- a short paraphrase, not a
# restated job description.
MAX_TEXT_CHARS = 500

# Per-item bounds on the three bounded term lists (diagnostic terms, equivalents/synonyms,
# preserved acronyms/technologies/standards) -- deliberately small: these are retrieval hints, not
# an open-ended keyword dump.
MAX_DIAGNOSTIC_TERMS = 8
MAX_EQUIVALENTS = 6
MAX_PRESERVED_TERMS = 10

# No single term/phrase in any of the three lists above may exceed this length.
MAX_TERM_CHARS = 60
