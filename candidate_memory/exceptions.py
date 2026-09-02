"""Domain-level exceptions for the Candidate Memory lifecycle (docs/ARCHITECTURE.md Sec 4,
M0.1 audit fix). These are raised by model-level guards so that "an ACTIVE/SUPERSEDED revision
cannot be edited" holds no matter which layer (service, view, form, admin, shell) attempts it.
"""


class RevisionNotEditableError(Exception):
    """Raised when code attempts to create/modify/delete content scoped to a CandidateMemory
    revision that is not in a mutable (BUILDING/NEEDS_REVIEW) state."""


class SourceDocumentImmutableError(Exception):
    """Raised when code attempts to modify a MemorySourceDocument after creation. Source
    occurrences are immutable from the moment they are stored, regardless of revision status."""


class InvalidActivationError(Exception):
    """Raised when activation is attempted but a precondition is not met (e.g. a validation
    failure that could let unsupported content become eligible)."""


class ExistingWorkingRevisionError(Exception):
    """Raised by bootstrap when a BUILDING/NEEDS_REVIEW revision already exists (audit repair:
    bootstrap idempotency/recovery) -- refuses to silently create a second, orphaned working
    revision. The message names the existing revision's version and the safe next action
    (activate it, or explicitly abandon it via `services.revision.abandon_revision`)."""
