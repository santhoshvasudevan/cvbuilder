"""Strict machine-readable handoff validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SchemaError(ValueError):
    pass


IMPLEMENTER_STATUSES = frozenset({"IMPLEMENTED", "QUESTION", "BLOCKED", "FAILED"})
AUDIT_VERDICTS = frozenset({"PASS", "CORRECTION_REQUIRED", "BLOCKED"})
MERGE_RECOMMENDATIONS = frozenset({"MERGE", "DO_NOT_MERGE", "OPERATOR_REVIEW"})
SEVERITIES = frozenset({"BLOCKER", "HIGH", "MEDIUM", "LOW", "INFO"})
FINDING_STATUSES = frozenset({"OPEN", "CLOSED"})
OPERATOR_DECISION_FIELDS = frozenset(
    {
        "run_id",
        "decision",
        "reason",
        "additional_allowed_paths",
        "constraints",
    }
)

PHASE_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "milestone",
        "slice",
        "base_sha",
        "product_baseline_sha",
        "orchestration_tooling_sha",
        "future_implementation_base_sha",
        "objective",
        "requirement_ids",
        "governing_decision_ids",
        "dependencies",
        "in_scope",
        "out_of_scope",
        "allowed_paths",
        "prohibited_paths",
        "acceptance_criteria",
        "required_tests",
        "required_documentation_updates",
        "operator_gates",
    }
)
IMPLEMENTER_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "base_sha",
        "result_sha",
        "session_id",
        "requirements_addressed",
        "files_changed",
        "migrations",
        "tests_added",
        "test_commands",
        "documentation_updated",
        "known_gaps",
        "questions",
        "decisions_required",
        "summary",
    }
)
IMPLEMENTER_NARRATIVE_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "summary",
        "known_gaps",
        "questions",
        "decisions_required",
    }
)
AUDIT_FIELDS = frozenset(
    {
        "schema_version",
        "verdict",
        "base_sha",
        "candidate_sha",
        "findings",
        "test_commands",
        "summary",
        "audit_test_requested",
        "audit_test_commit_sha",
    }
)
CLOSURE_FIELDS = frozenset(
    {
        "schema_version",
        "base_sha",
        "final_sha",
        "accepted_requirement_ids",
        "closed_findings",
        "unresolved_findings",
        "residual_risks",
        "deterministic_verification_results",
        "documentation_status",
        "merge_recommendation",
        "no_merge_or_push_confirmed",
        "summary",
    }
)


def load_json_document(value: str | bytes | Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        if isinstance(value, Path):
            raw = value.read_text(encoding="utf-8")
        else:
            raw = value.decode() if isinstance(value, bytes) else value
        result = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaError(f"Invalid JSON document: {exc}") from exc
    if not isinstance(result, dict):
        raise SchemaError("handoff document must be a JSON object")
    return result


def _strict_fields(value: dict[str, Any], fields: frozenset[str], name: str) -> None:
    unknown = set(value) - fields
    missing = fields - set(value)
    if unknown or missing:
        raise SchemaError(f"{name} fields invalid: unknown={sorted(unknown)}, missing={sorted(missing)}")
    if value.get("schema_version") != 1:
        raise SchemaError(f"{name}.schema_version must be 1")


def _string(value: dict, key: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value[key], str) or (not allow_empty and not value[key]):
        raise SchemaError(f"{key} must be {'a string' if allow_empty else 'a non-empty string'}")


def _string_list(value: dict, key: str) -> None:
    if not isinstance(value[key], list) or any(not isinstance(item, str) for item in value[key]):
        raise SchemaError(f"{key} must be a list of strings")


def _test_commands(value: dict) -> None:
    commands = value["test_commands"]
    if not isinstance(commands, list):
        raise SchemaError("test_commands must be a list")
    for command in commands:
        if set(command) != {"command", "exit_code"}:
            raise SchemaError("each test command requires only command and exit_code")
        if not isinstance(command["command"], str) or not isinstance(command["exit_code"], int):
            raise SchemaError("test command values have invalid types")


def validate_phase_contract(value: dict[str, Any]) -> dict[str, Any]:
    _strict_fields(value, PHASE_FIELDS, "phase_contract")
    for key in (
        "run_id",
        "milestone",
        "slice",
        "base_sha",
        "product_baseline_sha",
        "objective",
    ):
        _string(value, key)
    for key in ("orchestration_tooling_sha", "future_implementation_base_sha"):
        _string(value, key, allow_empty=True)
    for key in (
        "requirement_ids",
        "governing_decision_ids",
        "dependencies",
        "in_scope",
        "out_of_scope",
        "allowed_paths",
        "prohibited_paths",
        "acceptance_criteria",
        "required_tests",
        "required_documentation_updates",
        "operator_gates",
    ):
        _string_list(value, key)
    if len(value["base_sha"]) != 40 or len(value["product_baseline_sha"]) != 40:
        raise SchemaError("base SHA values must use full 40-character hashes")
    return value


def validate_operator_decision(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError("operator_decision must be an object")
    unknown = set(value) - OPERATOR_DECISION_FIELDS
    missing = OPERATOR_DECISION_FIELDS - set(value)
    if unknown or missing:
        raise SchemaError(
            "operator_decision fields invalid: "
            f"unknown={sorted(unknown)}, missing={sorted(missing)}"
        )
    for key in ("run_id", "reason"):
        _string(value, key)
    if value["decision"] != "APPROVED":
        raise SchemaError("operator_decision.decision must be APPROVED")
    for key in ("additional_allowed_paths", "constraints"):
        _string_list(value, key)
        if not value[key] or any(not item.strip() for item in value[key]):
            raise SchemaError(f"operator_decision.{key} must contain non-empty strings")
        if len(value[key]) != len(set(value[key])):
            raise SchemaError(f"operator_decision.{key} must not contain duplicates")
    for path in value["additional_allowed_paths"]:
        candidate = Path(path)
        if candidate.is_absolute() or ".." in candidate.parts or path.startswith("."):
            raise SchemaError("operator decision paths must be safe repository-relative paths")
    return value


def validate_implementer_response(value: dict[str, Any]) -> dict[str, Any]:
    _strict_fields(value, IMPLEMENTER_FIELDS, "implementer_response")
    if value["status"] not in IMPLEMENTER_STATUSES:
        raise SchemaError(f"Unknown implementer status: {value['status']!r}")
    for key in ("base_sha", "result_sha", "session_id", "summary"):
        _string(value, key, allow_empty=key in {"result_sha", "session_id"})
    for key in (
        "requirements_addressed",
        "files_changed",
        "migrations",
        "tests_added",
        "documentation_updated",
        "known_gaps",
        "questions",
        "decisions_required",
    ):
        _string_list(value, key)
    _test_commands(value)
    if value["status"] == "IMPLEMENTED" and not value["result_sha"]:
        raise SchemaError("IMPLEMENTED requires result_sha")
    if value["status"] == "QUESTION" and not value["questions"]:
        raise SchemaError("QUESTION requires at least one question")
    return value


def validate_implementer_narrative(value: dict[str, Any]) -> dict[str, Any]:
    """Validate Cursor's narrative subset; additional properties are allowed and ignored by callers."""
    if not isinstance(value, dict):
        raise SchemaError("implementer_narrative must be an object")
    missing = IMPLEMENTER_NARRATIVE_FIELDS - set(value)
    if missing:
        raise SchemaError(
            f"implementer_narrative fields invalid: missing={sorted(missing)}"
        )
    if value.get("schema_version") != 1:
        raise SchemaError("implementer_narrative.schema_version must be 1")
    if value["status"] not in IMPLEMENTER_STATUSES:
        raise SchemaError(f"Unknown implementer status: {value['status']!r}")
    _string(value, "summary")
    for key in ("known_gaps", "questions", "decisions_required"):
        _string_list(value, key)
    if value["status"] == "QUESTION" and not value["questions"]:
        raise SchemaError("QUESTION requires at least one question")
    return value


def validate_audit_response(value: dict[str, Any]) -> dict[str, Any]:
    _strict_fields(value, AUDIT_FIELDS, "audit_response")
    if value["verdict"] not in AUDIT_VERDICTS:
        raise SchemaError(f"Unknown audit verdict: {value['verdict']!r}")
    for key in ("base_sha", "candidate_sha", "summary", "audit_test_commit_sha"):
        _string(value, key, allow_empty=key == "audit_test_commit_sha")
    if not isinstance(value["audit_test_requested"], bool):
        raise SchemaError("audit_test_requested must be boolean")
    _test_commands(value)
    if not isinstance(value["findings"], list):
        raise SchemaError("findings must be a list")
    ids = set()
    required = {
        "finding_id",
        "severity",
        "requirement_ids",
        "decision_ids",
        "location",
        "explanation",
        "required_correction",
        "test_added",
        "test_commit_sha",
        "status",
    }
    for finding in value["findings"]:
        if not isinstance(finding, dict) or set(finding) != required:
            raise SchemaError("audit finding fields are malformed")
        if finding["finding_id"] in ids:
            raise SchemaError(f"duplicate finding ID: {finding['finding_id']}")
        ids.add(finding["finding_id"])
        if finding["severity"] not in SEVERITIES or finding["status"] not in FINDING_STATUSES:
            raise SchemaError(f"finding {finding['finding_id']} has invalid severity/status")
        if not isinstance(finding["test_added"], bool):
            raise SchemaError("finding.test_added must be boolean")
        for key in ("requirement_ids", "decision_ids"):
            if not isinstance(finding[key], list) or any(not isinstance(item, str) for item in finding[key]):
                raise SchemaError(f"finding.{key} must be a string list")
        for key in ("finding_id", "location", "explanation", "required_correction", "test_commit_sha"):
            if not isinstance(finding[key], str):
                raise SchemaError(f"finding.{key} must be a string")
        if finding["test_added"] != bool(finding["test_commit_sha"]):
            raise SchemaError("finding test_added and test_commit_sha must agree")
    if value["verdict"] == "PASS" and any(item["status"] == "OPEN" for item in value["findings"]):
        raise SchemaError("PASS cannot contain open findings")
    if value["verdict"] == "CORRECTION_REQUIRED" and not any(
        item["status"] == "OPEN" for item in value["findings"]
    ):
        raise SchemaError("CORRECTION_REQUIRED requires at least one open finding")
    if value["audit_test_requested"] and (
        value["verdict"] != "CORRECTION_REQUIRED" or value["audit_test_commit_sha"]
    ):
        raise SchemaError("audit test requests require an uncommitted correction finding")
    return value


def validate_closure_response(value: dict[str, Any]) -> dict[str, Any]:
    _strict_fields(value, CLOSURE_FIELDS, "closure_response")
    for key in ("base_sha", "final_sha", "documentation_status", "summary"):
        _string(value, key)
    for key in (
        "accepted_requirement_ids",
        "closed_findings",
        "unresolved_findings",
        "residual_risks",
        "deterministic_verification_results",
    ):
        _string_list(value, key)
    if value["merge_recommendation"] not in MERGE_RECOMMENDATIONS:
        raise SchemaError("invalid closure merge_recommendation")
    if value["no_merge_or_push_confirmed"] is not True:
        raise SchemaError("closure must explicitly confirm that no merge or push occurred")
    return value
