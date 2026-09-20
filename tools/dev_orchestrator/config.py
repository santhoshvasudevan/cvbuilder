"""Strict YAML configuration loading for the external development orchestrator."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Configuration is malformed or contains an unsupported value."""


ROLE_NAMES = frozenset(
    {
        "orcha",
        "orcha_closure",
        "implementer",
        "reviewer",
        "codex_reviewer",
        "escalation_reviewer",
        "architecture_escalation",
        "claude_orchestrator",
        "claude_reviewer",
    }
)
ADAPTER_NAMES = frozenset({"codex", "cursor", "claude", "fake"})
PERMISSION_PROFILES = frozenset({"read_only", "implementation_worktree", "audit_worktree"})


def _exact_keys(value: dict[str, Any], expected: set[str], context: str) -> None:
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown or missing:
        details = []
        if unknown:
            details.append(f"unknown={sorted(unknown)}")
        if missing:
            details.append(f"missing={sorted(missing)}")
        raise ConfigError(f"{context} keys invalid ({', '.join(details)})")


def _positive_int(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{context} must be a positive integer")
    return value


@dataclasses.dataclass(frozen=True)
class RepositoryConfig:
    default_branch: str
    bootstrap_branch: str
    bootstrap_base_sha: str
    require_clean_base: bool
    automatic_merge: bool


@dataclasses.dataclass(frozen=True)
class AgentConfig:
    adapter: str
    binary: str
    model: str
    permission_profile: str
    enabled: bool = True
    reasoning_effort: str | None = None


@dataclasses.dataclass(frozen=True)
class LimitsConfig:
    max_correction_cycles: int
    max_question_cycles: int
    max_agent_runtime_minutes: int
    heartbeat_seconds: int
    stop_on_repeated_finding: bool


@dataclasses.dataclass(frozen=True)
class OperatorGatesConfig:
    approve_phase_contract: bool
    approve_first_live_implementation: bool
    approve_audit_test_transfer: bool
    approve_merge: bool


@dataclasses.dataclass(frozen=True)
class OrchestratorConfig:
    version: int
    repository: RepositoryConfig
    agents: dict[str, AgentConfig]
    limits: LimitsConfig
    operator_gates: OperatorGatesConfig


def _agent_from_dict(role: str, raw: Any) -> AgentConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"agents.{role} must be an object")
    required = {"adapter", "binary", "model", "permission_profile"}
    optional = {"enabled", "reasoning_effort"}
    unknown = set(raw) - required - optional
    missing = required - set(raw)
    if unknown or missing:
        raise ConfigError(
            f"agents.{role} keys invalid (unknown={sorted(unknown)}, missing={sorted(missing)})"
        )
    adapter = raw["adapter"]
    if adapter not in ADAPTER_NAMES:
        raise ConfigError(f"agents.{role}.adapter is unsupported: {adapter!r}")
    permission = raw["permission_profile"]
    if permission not in PERMISSION_PROFILES:
        raise ConfigError(f"agents.{role}.permission_profile is unsupported: {permission!r}")
    if adapter == "cursor" and permission != "implementation_worktree":
        raise ConfigError("Cursor roles must use implementation_worktree")
    if role == "implementer" and adapter not in {"cursor", "fake"}:
        raise ConfigError("implementer must use the cursor adapter (or fake in tests)")
    if role == "reviewer" and permission != "audit_worktree":
        raise ConfigError("reviewer must use audit_worktree")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"agents.{role}.enabled must be boolean")
    for key in ("binary", "model"):
        if not isinstance(raw[key], str) or not raw[key].strip():
            raise ConfigError(f"agents.{role}.{key} must be a non-empty string")
    effort = raw.get("reasoning_effort")
    if effort is not None and effort not in {"low", "medium", "high", "xhigh"}:
        raise ConfigError(f"agents.{role}.reasoning_effort is invalid: {effort!r}")
    return AgentConfig(
        adapter=adapter,
        binary=raw["binary"],
        model=raw["model"],
        permission_profile=permission,
        enabled=enabled,
        reasoning_effort=effort,
    )


def load_config(path: str | Path) -> OrchestratorConfig:
    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Unable to load {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("configuration root must be an object")
    _exact_keys(raw, {"version", "repository", "agents", "limits", "operator_gates"}, "root")
    if raw["version"] != 1:
        raise ConfigError(f"Unsupported configuration version: {raw['version']!r}")

    repository = raw["repository"]
    if not isinstance(repository, dict):
        raise ConfigError("repository must be an object")
    _exact_keys(
        repository,
        {
            "default_branch",
            "bootstrap_branch",
            "bootstrap_base_sha",
            "require_clean_base",
            "automatic_merge",
        },
        "repository",
    )
    if repository["automatic_merge"] is not False:
        raise ConfigError("repository.automatic_merge must remain false")
    if not isinstance(repository["require_clean_base"], bool):
        raise ConfigError("repository.require_clean_base must be boolean")
    if repository["require_clean_base"] is not True:
        raise ConfigError("repository.require_clean_base must remain true")
    base_sha = repository["bootstrap_base_sha"]
    if not isinstance(base_sha, str) or re.fullmatch(r"[0-9a-f]{40}", base_sha) is None:
        raise ConfigError("repository.bootstrap_base_sha must be a full 40-character SHA")
    for key in ("default_branch", "bootstrap_branch"):
        if not isinstance(repository[key], str) or not repository[key].strip():
            raise ConfigError(f"repository.{key} must be a non-empty string")

    agents = raw["agents"]
    if not isinstance(agents, dict):
        raise ConfigError("agents must be an object")
    if set(agents) != ROLE_NAMES:
        raise ConfigError(
            f"agent roles invalid (unknown={sorted(set(agents) - ROLE_NAMES)}, "
            f"missing={sorted(ROLE_NAMES - set(agents))})"
        )
    parsed_agents = {role: _agent_from_dict(role, value) for role, value in agents.items()}

    limits = raw["limits"]
    if not isinstance(limits, dict):
        raise ConfigError("limits must be an object")
    limit_keys = {
        "max_correction_cycles",
        "max_question_cycles",
        "max_agent_runtime_minutes",
        "heartbeat_seconds",
        "stop_on_repeated_finding",
    }
    _exact_keys(limits, limit_keys, "limits")
    if not isinstance(limits["stop_on_repeated_finding"], bool):
        raise ConfigError("limits.stop_on_repeated_finding must be boolean")

    gates = raw["operator_gates"]
    if not isinstance(gates, dict):
        raise ConfigError("operator_gates must be an object")
    gate_keys = {
        "approve_phase_contract",
        "approve_first_live_implementation",
        "approve_audit_test_transfer",
        "approve_merge",
    }
    _exact_keys(gates, gate_keys, "operator_gates")
    if any(not isinstance(gates[key], bool) for key in gate_keys):
        raise ConfigError("all operator_gates values must be boolean")
    if any(gates[key] is not True for key in gate_keys):
        raise ConfigError("all operator gates must remain enabled")

    return OrchestratorConfig(
        version=1,
        repository=RepositoryConfig(**repository),
        agents=parsed_agents,
        limits=LimitsConfig(
            max_correction_cycles=_positive_int(
                limits["max_correction_cycles"], "limits.max_correction_cycles"
            ),
            max_question_cycles=_positive_int(limits["max_question_cycles"], "limits.max_question_cycles"),
            max_agent_runtime_minutes=_positive_int(
                limits["max_agent_runtime_minutes"], "limits.max_agent_runtime_minutes"
            ),
            heartbeat_seconds=_positive_int(limits["heartbeat_seconds"], "limits.heartbeat_seconds"),
            stop_on_repeated_finding=limits["stop_on_repeated_finding"],
        ),
        operator_gates=OperatorGatesConfig(**gates),
    )
