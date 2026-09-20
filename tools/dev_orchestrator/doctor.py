"""Read-only environment and configured-adapter qualification."""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
from pathlib import Path

from .adapters.base import ProcessAdapter
from .adapters.claude_schema import schema_argument_for_claude_cli
from .config import OrchestratorConfig
from .git_safety import GitRepository, GitSafetyError
from .redaction import REDACTED, redact_text
from .schemas import SchemaError, validate_audit_response


@dataclasses.dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _command(argv: list[str], cwd: Path) -> tuple[int, str]:
    try:
        result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return result.returncode, (result.stdout + result.stderr).strip()


def _claude_dry_audit(binary: str, model: str, root: Path, schema_path: Path) -> tuple[int, str]:
    """Make the minimal explicit live call required to qualify Claude structured output."""
    schema_argument = schema_argument_for_claude_cli(schema_path)
    prompt = (
        "Return only a schema-valid audit response for this CLI qualification. Use schema_version "
        "1, verdict PASS, base_sha "
        + "a" * 40
        + ", candidate_sha "
        + "b" * 40
        + ", no findings, test_commands containing git status --short with exit_code 0, "
        "summary 'Claude structured-output qualification passed', audit_test_requested false, "
        "and an empty audit_test_commit_sha."
    )
    try:
        result = subprocess.run(
            [
                binary,
                "--safe-mode",
                "--print",
                "--output-format",
                "json",
                "--json-schema",
                schema_argument,
                "--model",
                model,
                "--permission-mode",
                "plan",
                "--permission-prompts",
                "none",
                "--tools",
                "",
            ],
            cwd=root,
            input=prompt,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
            env=ProcessAdapter.safe_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, redact_text(str(exc))
    if result.returncode != 0:
        return result.returncode, redact_text(result.stderr.strip() or result.stdout.strip())

    event = None
    for line in reversed(result.stdout.splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            event = candidate
            break
    if event is None:
        return 1, "Claude returned no JSON result event"
    payload = event.get("structured_output")
    if not isinstance(payload, dict):
        raw_result = event.get("result")
        if isinstance(raw_result, str):
            try:
                payload = json.loads(raw_result)
            except json.JSONDecodeError:
                payload = None
    if not isinstance(payload, dict):
        return 1, "Claude result did not contain a structured JSON object"
    try:
        validate_audit_response(payload)
    except SchemaError as exc:
        return 1, f"Claude dry audit failed controller validation: {exc}"
    return 0, "audit-response schema and controller validation passed"


def run_doctor(root: Path, runtime_root: Path, config: OrchestratorConfig) -> list[Check]:
    checks: list[Check] = []
    checks.append(Check("repository path", "PASS" if (root / ".git").exists() else "FAIL", str(root)))
    repository = GitRepository(root)
    try:
        boundary = repository.verify_bootstrap_boundary(
            default_branch=config.repository.default_branch,
            bootstrap_branch=config.repository.bootstrap_branch,
            bootstrap_base_sha=config.repository.bootstrap_base_sha,
            require_clean=False,
        )
    except GitSafetyError as exc:
        checks.append(Check("Git boundary", "FAIL", str(exc)))
    else:
        checks.append(Check("Git boundary", "PASS", f"branch={boundary.branch} HEAD={boundary.head}"))
        checks.append(
            Check(
                "worktree cleanliness",
                "PASS" if boundary.clean else "WARN",
                "clean" if boundary.clean else "dirty; live approval will be refused",
            )
        )
    for binary in ("git", "tmux", "jq"):
        resolved = shutil.which(binary)
        checks.append(Check(binary, "PASS" if resolved else "FAIL", resolved or "not found"))

    codex = config.agents["orcha"].binary
    code, help_text = _command([codex, "exec", "--help"], root)
    required_flags = ("--model", "--sandbox", "--json", "--output-schema", "--output-last-message")
    capability_ok = code == 0 and all(flag in help_text for flag in required_flags)
    checks.append(
        Check(
            "Codex machine-readable execution",
            "PASS" if capability_ok else "FAIL",
            "required exec flags present" if capability_ok else "required flags missing",
        )
    )
    login_code, login_text = _command([codex, "login", "status"], root)
    checks.append(
        Check(
            "Codex authentication",
            "PASS" if login_code == 0 and "logged in" in login_text.lower() else "WARN",
            redact_text(login_text),
        )
    )
    codex_roles = (
        "orcha",
        "orcha_closure",
        "reviewer",
        "codex_reviewer",
        "escalation_reviewer",
        "architecture_escalation",
    )
    checks.append(
        Check(
            "Codex models",
            "PASS",
            "configured syntactically; availability is intentionally not tested without a live call: "
            + ", ".join(
                f"{role}={config.agents[role].model}"
                for role in codex_roles
                if config.agents[role].adapter == "codex" and config.agents[role].enabled
            ),
        )
    )

    cursor = config.agents["implementer"].binary
    cursor_exists = Path(cursor).is_file() and os.access(cursor, os.X_OK)
    checks.append(Check("Cursor binary", "PASS" if cursor_exists else "FAIL", cursor))
    if cursor_exists:
        help_code, cursor_help = _command([cursor, "--help"], root)
        cursor_flags = ("--print", "stream-json", "--model", "--resume", "--workspace")
        cursor_capable = help_code == 0 and all(flag in cursor_help for flag in cursor_flags)
        checks.append(
            Check(
                "Cursor machine-readable execution",
                "PASS" if cursor_capable else "FAIL",
                "required print/stream/resume flags present" if cursor_capable else "required flags missing",
            )
        )
        status_code, status_text = _command([cursor, "status"], root)
        logged_in = status_code == 0 and "not logged in" not in status_text.lower()
        checks.append(
            Check(
                "Cursor authentication",
                "PASS" if logged_in else "WARN",
                redact_text(status_text) or "status unavailable",
            )
        )
        checks.append(
            Check(
                "Cursor model",
                "PASS",
                f"configured={config.agents['implementer'].model}; live availability not queried",
            )
        )

    claude_orchestrator = config.agents["claude_orchestrator"]
    checks.append(
        Check(
            "claude_orchestrator",
            "SKIP" if not claude_orchestrator.enabled else "WARN",
            (
                "disabled; binary/authentication not checked"
                if not claude_orchestrator.enabled
                else "enabled but not assigned to an active controller role"
            ),
        )
    )
    codex_reviewer = config.agents["codex_reviewer"]
    checks.append(
        Check(
            "codex_reviewer profile",
            "SKIP" if not codex_reviewer.enabled else "WARN",
            "disabled rollback profile" if not codex_reviewer.enabled else "unexpectedly enabled",
        )
    )

    reviewer = config.agents["reviewer"]
    claude_live_call = False
    if reviewer.adapter != "claude" or not reviewer.enabled:
        checks.append(
            Check(
                "Claude reviewer routing",
                "FAIL",
                "reviewer role is not routed to an enabled Claude profile",
            )
        )
    else:
        resolved = shutil.which(reviewer.binary)
        checks.append(Check("Claude binary", "PASS" if resolved else "FAIL", resolved or "not found"))
        if resolved:
            version_code, version_text = _command([reviewer.binary, "--version"], root)
            checks.append(
                Check(
                    "Claude version",
                    "PASS" if version_code == 0 and version_text else "FAIL",
                    redact_text(version_text) or "version unavailable",
                )
            )
            help_code, help_text = _command([reviewer.binary, "--help"], root)
            required = ("--print", "--output-format", "--json-schema", "--permission-mode")
            schema_capable = help_code == 0 and all(flag in help_text for flag in required)
            checks.append(
                Check(
                    "Claude structured-output flags",
                    "PASS" if schema_capable else "FAIL",
                    "required print/JSON/schema/permission flags present"
                    if schema_capable
                    else "required flags missing",
                )
            )
            if schema_capable:
                dry_code, dry_detail = _claude_dry_audit(
                    reviewer.binary,
                    reviewer.model,
                    root,
                    root / "tools/dev_orchestrator/schemas/audit-response.schema.json",
                )
                claude_live_call = True
                checks.append(
                    Check(
                        "Claude dry audit",
                        "PASS" if dry_code == 0 else "FAIL",
                        dry_detail,
                    )
                )
    writable = (
        os.access(runtime_root, os.W_OK) if runtime_root.exists() else os.access(runtime_root.parent, os.W_OK)
    )
    checks.append(Check("runtime directories", "PASS" if writable else "FAIL", str(runtime_root)))
    worktrees = repository.worktrees()
    distinct = len({item.get("worktree", "") for item in worktrees}) == len(worktrees)
    checks.append(Check("worktree registration", "PASS" if distinct else "FAIL", "registered paths unique"))
    redacted_sample = redact_text("token=sample-secret-value")
    redaction_ok = "sample-secret-value" not in redacted_sample and REDACTED in redacted_sample
    checks.append(Check("log redaction", "PASS" if redaction_ok else "FAIL", "self-test passed"))
    checks.append(
        Check(
            "live model calls",
            "PASS" if claude_live_call else "SKIP",
            "Claude reviewer dry audit invoked" if claude_live_call else "not invoked",
        )
    )
    return checks
