"""Read-only environment and configured-adapter qualification."""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .adapters.base import ProcessAdapter
from .adapters.claude_schema import schema_argument_for_claude_cli
from .config import AgentConfig, OrchestratorConfig
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


def _codex_dry_audit(binary: str, model: str, root: Path, schema_path: Path) -> tuple[int, str]:
    """Make the minimal explicit live call required to qualify Codex structured output."""
    prompt = (
        "Return only a schema-valid audit response for this CLI qualification. Use schema_version "
        "1, verdict PASS, base_sha "
        + "a" * 40
        + ", candidate_sha "
        + "b" * 40
        + ", no findings, test_commands containing git status --short with exit_code 0, "
        "summary 'Codex structured-output qualification passed', audit_test_requested false, "
        "and an empty audit_test_commit_sha."
    )
    with tempfile.TemporaryDirectory(prefix="doctor-codex-dry-audit-") as tmp:
        final_path = Path(tmp) / "final.json"
        try:
            result = subprocess.run(
                [
                    binary,
                    "exec",
                    "-C",
                    str(root),
                    "--sandbox",
                    "read-only",
                    "-m",
                    model,
                    "--json",
                    "--output-last-message",
                    str(final_path),
                    "--output-schema",
                    str(schema_path),
                    "-",
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

        payload = None
        if final_path.is_file():
            payload = ProcessAdapter._parse_handoff(final_path.read_text(encoding="utf-8"))
        if payload is None:
            # Prefer the output-last-message file; fall back to scanning stdout for a JSON object.
            for line in reversed(result.stdout.splitlines()):
                try:
                    candidate = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(candidate, dict):
                    continue
                text = candidate.get("text") or candidate.get("message") or candidate.get("result")
                if isinstance(text, str):
                    payload = ProcessAdapter._parse_handoff(text)
                item = candidate.get("item")
                if payload is None and isinstance(item, dict):
                    item_text = item.get("text") or item.get("content")
                    if isinstance(item_text, str):
                        payload = ProcessAdapter._parse_handoff(item_text)
                if payload is None and "schema_version" in candidate:
                    payload = candidate
                if payload is not None:
                    break
        if not isinstance(payload, dict):
            return 1, "Codex result did not contain a structured JSON object"
        try:
            validate_audit_response(payload)
        except SchemaError as exc:
            return 1, f"Codex dry audit failed controller validation: {exc}"
        return 0, "audit-response schema and controller validation passed"


def _profiles_match(reviewer: AgentConfig, profile: AgentConfig) -> bool:
    return (
        reviewer.adapter == profile.adapter
        and reviewer.binary == profile.binary
        and reviewer.model == profile.model
        and reviewer.reasoning_effort == profile.reasoning_effort
        and reviewer.permission_profile == profile.permission_profile
    )


def _qualify_claude_reviewer(checks: list[Check], reviewer: AgentConfig, root: Path) -> bool:
    live_call = False
    resolved = shutil.which(reviewer.binary)
    checks.append(Check("Claude binary", "PASS" if resolved else "FAIL", resolved or "not found"))
    if not resolved:
        return live_call
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
        live_call = True
        checks.append(
            Check(
                "Claude dry audit",
                "PASS" if dry_code == 0 else "FAIL",
                dry_detail,
            )
        )
    return live_call


def _qualify_codex_reviewer(checks: list[Check], reviewer: AgentConfig, root: Path) -> bool:
    live_call = False
    resolved = shutil.which(reviewer.binary)
    checks.append(Check("Codex reviewer binary", "PASS" if resolved else "FAIL", resolved or "not found"))
    if not resolved:
        return live_call
    help_code, help_text = _command([reviewer.binary, "exec", "--help"], root)
    required_flags = ("--model", "--sandbox", "--json", "--output-schema", "--output-last-message")
    capability_ok = help_code == 0 and all(flag in help_text for flag in required_flags)
    checks.append(
        Check(
            "Codex reviewer machine-readable execution",
            "PASS" if capability_ok else "FAIL",
            "required exec flags present" if capability_ok else "required flags missing",
        )
    )
    login_code, login_text = _command([reviewer.binary, "login", "status"], root)
    checks.append(
        Check(
            "Codex reviewer authentication",
            "PASS" if login_code == 0 and "logged in" in login_text.lower() else "WARN",
            redact_text(login_text),
        )
    )
    if capability_ok:
        dry_code, dry_detail = _codex_dry_audit(
            reviewer.binary,
            reviewer.model,
            root,
            root / "tools/dev_orchestrator/schemas/audit-response.schema.json",
        )
        live_call = True
        checks.append(
            Check(
                "Codex dry audit",
                "PASS" if dry_code == 0 else "FAIL",
                dry_detail,
            )
        )
    return live_call


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

    reviewer = config.agents["reviewer"]
    claude_profile = config.agents["claude_reviewer"]
    codex_profile = config.agents["codex_reviewer"]
    enabled_profiles = [
        (name, profile)
        for name, profile in (
            ("claude_reviewer", claude_profile),
            ("codex_reviewer", codex_profile),
        )
        if profile.enabled
    ]
    live_call = False
    live_detail = "not invoked"
    if len(enabled_profiles) != 1:
        enabled_names = [name for name, _ in enabled_profiles] or ["(none)"]
        checks.append(
            Check(
                "reviewer profile selection",
                "FAIL",
                "exactly one of claude_reviewer/codex_reviewer must be enabled; "
                f"enabled={','.join(enabled_names)}",
            )
        )
    elif not reviewer.enabled:
        checks.append(
            Check(
                "reviewer routing",
                "FAIL",
                "reviewer role must be enabled when a reviewer profile is selected",
            )
        )
    else:
        profile_name, profile = enabled_profiles[0]
        if not _profiles_match(reviewer, profile):
            checks.append(
                Check(
                    "reviewer routing",
                    "FAIL",
                    f"agents.reviewer does not route to the enabled profile {profile_name} "
                    f"(adapter/binary/model mismatch)",
                )
            )
        else:
            checks.append(
                Check(
                    "reviewer routing",
                    "PASS",
                    f"routed to enabled {profile_name} (adapter={profile.adapter})",
                )
            )
            if profile.adapter == "claude":
                live_call = _qualify_claude_reviewer(checks, reviewer, root)
                if live_call:
                    live_detail = "Claude reviewer dry audit invoked"
            elif profile.adapter == "codex":
                live_call = _qualify_codex_reviewer(checks, reviewer, root)
                if live_call:
                    live_detail = "Codex reviewer dry audit invoked"
            else:
                checks.append(
                    Check(
                        "reviewer adapter qualification",
                        "FAIL",
                        f"unsupported reviewer adapter: {profile.adapter!r}",
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
            "PASS" if live_call else "SKIP",
            live_detail,
        )
    )
    return checks
