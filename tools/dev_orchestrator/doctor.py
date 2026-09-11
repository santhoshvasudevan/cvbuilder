"""Read-only environment qualification; never invokes a model."""

from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
from pathlib import Path

from .config import OrchestratorConfig
from .git_safety import GitRepository, GitSafetyError
from .redaction import REDACTED, redact_text


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
    checks.append(
        Check(
            "Codex models",
            "PASS",
            "configured syntactically; availability is intentionally not tested without a live call: "
            + ", ".join(
                f"{role}={config.agents[role].model}"
                for role in (
                    "orcha",
                    "orcha_closure",
                    "reviewer",
                    "escalation_reviewer",
                    "architecture_escalation",
                )
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

    for role in ("claude_orchestrator", "claude_reviewer"):
        agent = config.agents[role]
        checks.append(
            Check(
                role,
                "SKIP" if not agent.enabled else "WARN",
                (
                    "disabled; binary/authentication not checked"
                    if not agent.enabled
                    else "enabled but unqualified"
                ),
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
    checks.append(Check("live model calls", "SKIP", "not invoked; explicit opt-in required"))
    return checks
