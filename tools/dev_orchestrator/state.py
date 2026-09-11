"""Durable orchestration state and transition rules."""

from __future__ import annotations

import dataclasses
import enum
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class StateError(ValueError):
    pass


class RunStateName(str, enum.Enum):
    PREPARING = "PREPARING"
    AWAITING_PHASE_APPROVAL = "AWAITING_PHASE_APPROVAL"
    IMPLEMENTING = "IMPLEMENTING"
    IMPLEMENTER_QUESTION = "IMPLEMENTER_QUESTION"
    ORCHA_DECISION = "ORCHA_DECISION"
    VALIDATING_IMPLEMENTATION = "VALIDATING_IMPLEMENTATION"
    AUDITING = "AUDITING"
    CORRECTION_REQUIRED = "CORRECTION_REQUIRED"
    ORCHA_CORRECTION_CONTRACT = "ORCHA_CORRECTION_CONTRACT"
    CORRECTING = "CORRECTING"
    CLOSURE_REVIEW = "CLOSURE_REVIEW"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    OPERATOR_ESCALATION = "OPERATOR_ESCALATION"
    ABORTED = "ABORTED"


TERMINAL_STATES = frozenset(
    {
        RunStateName.COMPLETED,
        RunStateName.BLOCKED,
        RunStateName.OPERATOR_ESCALATION,
        RunStateName.ABORTED,
    }
)

TRANSITIONS = {
    RunStateName.PREPARING: {RunStateName.AWAITING_PHASE_APPROVAL, RunStateName.BLOCKED},
    RunStateName.AWAITING_PHASE_APPROVAL: {RunStateName.IMPLEMENTING},
    RunStateName.IMPLEMENTING: {
        RunStateName.IMPLEMENTER_QUESTION,
        RunStateName.VALIDATING_IMPLEMENTATION,
        RunStateName.BLOCKED,
        RunStateName.OPERATOR_ESCALATION,
    },
    RunStateName.IMPLEMENTER_QUESTION: {RunStateName.ORCHA_DECISION},
    RunStateName.ORCHA_DECISION: {
        RunStateName.IMPLEMENTING,
        RunStateName.CORRECTING,
        RunStateName.BLOCKED,
        RunStateName.OPERATOR_ESCALATION,
    },
    RunStateName.VALIDATING_IMPLEMENTATION: {
        RunStateName.AUDITING,
        RunStateName.BLOCKED,
        RunStateName.OPERATOR_ESCALATION,
    },
    RunStateName.AUDITING: {
        RunStateName.CORRECTION_REQUIRED,
        RunStateName.CLOSURE_REVIEW,
        RunStateName.BLOCKED,
        RunStateName.OPERATOR_ESCALATION,
    },
    RunStateName.CORRECTION_REQUIRED: {RunStateName.ORCHA_CORRECTION_CONTRACT},
    RunStateName.ORCHA_CORRECTION_CONTRACT: {
        RunStateName.CORRECTING,
        RunStateName.OPERATOR_ESCALATION,
    },
    RunStateName.CORRECTING: {
        RunStateName.VALIDATING_IMPLEMENTATION,
        RunStateName.IMPLEMENTER_QUESTION,
        RunStateName.BLOCKED,
        RunStateName.OPERATOR_ESCALATION,
    },
    RunStateName.CLOSURE_REVIEW: {
        RunStateName.COMPLETED,
        RunStateName.BLOCKED,
        RunStateName.OPERATOR_ESCALATION,
    },
    RunStateName.OPERATOR_ESCALATION: {
        RunStateName.ORCHA_DECISION,
        RunStateName.AUDITING,
    },
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclasses.dataclass
class RunState:
    run_id: str
    phase: str
    state: str = RunStateName.PREPARING.value
    base_sha: str = ""
    result_sha: str = ""
    correction_cycles: int = 0
    question_cycles: int = 0
    finding_occurrences: dict[str, int] = dataclasses.field(default_factory=dict)
    sessions: dict[str, str] = dataclasses.field(default_factory=dict)
    controller_sha: str = ""
    original_contract_sha256: str = ""
    operator_decision_count: int = 0
    pending_operator_decision_path: str = ""
    recovered_handoff_path: str = ""
    latest_orcha_prompt_path: str = ""
    latest_orcha_prompt_sha256: str = ""
    pending_implementer_prompt_path: str = ""
    last_error: str = ""
    created_at: str = dataclasses.field(default_factory=utc_now)
    updated_at: str = dataclasses.field(default_factory=utc_now)

    def transition(self, target: RunStateName, reason: str = "") -> None:
        current = RunStateName(self.state)
        if target == RunStateName.ABORTED and current not in TERMINAL_STATES:
            pass
        elif target not in TRANSITIONS.get(current, set()):
            raise StateError(f"Invalid state transition: {current.value} -> {target.value}")
        self.state = target.value
        self.updated_at = utc_now()
        if reason:
            self.last_error = reason

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RunState":
        known = {field.name for field in dataclasses.fields(cls)}
        unknown = set(value) - known
        if unknown:
            raise StateError(f"Unknown state fields: {sorted(unknown)}")
        result = cls(**value)
        RunStateName(result.state)
        return result


class StateStore:
    """Atomic JSON state persistence: fsync temp file, replace, then fsync directory."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> RunState:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"Unable to load durable state {self.path}: {exc}") from exc
        if not isinstance(value, dict):
            raise StateError("state document must be an object")
        return RunState.from_dict(value)

    def save(self, state: RunState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(dataclasses.asdict(state), indent=2, sort_keys=True) + "\n"
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
