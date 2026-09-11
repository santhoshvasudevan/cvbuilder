"""Deterministic candidate evidence collection and enforcement."""

from __future__ import annotations

import dataclasses
import fnmatch
import shlex
import subprocess
from pathlib import Path
from typing import Callable

from .git_safety import GitRepository


class EvidenceError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class EvidenceReport:
    base_sha: str
    result_sha: str
    branch: str
    base_is_ancestor: bool
    commits: tuple[str, ...]
    changed_files: tuple[str, ...]
    untracked_files: tuple[str, ...]
    worktree_clean: bool
    prohibited_changes: tuple[str, ...]
    requirement_or_decision_changes: tuple[str, ...]
    suspicious_test_changes: tuple[str, ...]
    test_commands: tuple[tuple[str, int], ...]

    @property
    def accepted(self) -> bool:
        return (
            self.base_is_ancestor
            and self.worktree_clean
            and not self.prohibited_changes
            and not self.requirement_or_decision_changes
            and not self.suspicious_test_changes
            and all(exit_code == 0 for _, exit_code in self.test_commands)
        )


class EvidenceCollector:
    def __init__(
        self,
        repository: GitRepository,
        command_runner: Callable[[list[str], Path], int] | None = None,
    ):
        self.repository = repository
        self.command_runner = command_runner or self._run_command

    @staticmethod
    def _run_command(argv: list[str], worktree: Path) -> int:
        resolved_argv = list(argv)
        if argv[0] == "make":
            common_dir = subprocess.run(
                ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                cwd=worktree,
                text=True,
                capture_output=True,
                check=False,
            ).stdout.strip()
            repository_root = Path(common_dir).parent if common_dir else worktree
            resolved_argv = [
                "make",
                f"VENV_PYTHON={repository_root / '.venv/bin/python'}",
                f"VENV_RUFF={repository_root / '.venv/bin/ruff'}",
                f"VENV_DETECT_SECRETS={repository_root / '.venv/bin/detect-secrets'}",
                *argv[1:],
            ]
        elif argv[0].startswith(".venv/"):
            common_dir = subprocess.run(
                ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                cwd=worktree,
                text=True,
                capture_output=True,
                check=False,
            ).stdout.strip()
            repository_root = Path(common_dir).parent if common_dir else worktree
            resolved_argv[0] = str(repository_root / argv[0])
        try:
            result = subprocess.run(
                resolved_argv,
                cwd=worktree,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=3600,
            )
        except (OSError, subprocess.TimeoutExpired):
            return 124
        return result.returncode

    @staticmethod
    def _validate_verification_argv(argv: list[str]) -> None:
        executable = Path(argv[0]).name
        if executable == "make":
            allowed_targets = {
                "verify",
                "test",
                "check",
                "lint",
                "migrations-check",
                "secrets",
            }
            if len(argv) != 2 or argv[1] not in allowed_targets:
                raise EvidenceError(f"unapproved make verification command: {argv!r}")
            return
        if executable == "git" and argv[1:] in (["diff", "--check"], ["status", "--short"]):
            return
        if executable.startswith("python") and len(argv) >= 3:
            if argv[1] == "manage.py" and argv[2] in {"test", "check"}:
                return
            if argv[1:3] == ["manage.py", "makemigrations"] and {
                "--check",
                "--dry-run",
            }.issubset(argv):
                return
            if argv[1:3] == ["-m", "unittest"]:
                return
        raise EvidenceError(f"unapproved verification executable/arguments: {argv!r}")

    def _execute_test_commands(self, worktree: Path, reported: list[dict]) -> tuple[tuple[str, int], ...]:
        observed = []
        for item in reported:
            command = item["command"]
            try:
                argv = shlex.split(command)
            except ValueError as exc:
                raise EvidenceError(f"verification command is not parseable: {command!r}: {exc}") from exc
            if not argv:
                raise EvidenceError("verification command cannot be empty")
            # Shell operators are never accepted: every verification runs as an argument array.
            if any(token in {"|", "||", "&&", ";", ">", ">>", "<", "`"} for token in argv):
                raise EvidenceError(f"verification command requires forbidden shell syntax: {command!r}")
            self._validate_verification_argv(argv)
            exit_code = self.command_runner(argv, worktree)
            observed.append((command, exit_code))
            if exit_code != item["exit_code"]:
                raise EvidenceError(
                    f"reported exit code for {command!r} was {item['exit_code']}, observed {exit_code}"
                )
        return tuple(observed)

    def validate_test_commands(self, worktree: Path, reported: list[dict]) -> tuple[tuple[str, int], ...]:
        """Re-run an agent's allowlisted verification commands and compare its exit codes."""
        return self._execute_test_commands(worktree, reported)

    @staticmethod
    def _matches(path: str, patterns: list[str]) -> bool:
        return any(
            path == pattern.rstrip("/")
            or path.startswith(pattern.rstrip("/") + "/")
            or fnmatch.fnmatch(path, pattern)
            for pattern in patterns
        )

    def collect_and_validate(
        self,
        *,
        worktree: Path,
        base_sha: str,
        result_sha: str,
        allowed_paths: list[str],
        prohibited_paths: list[str],
        test_commands: list[dict],
        authorized_governance_changes: bool = False,
        authorized_test_changes: list[str] | None = None,
    ) -> EvidenceReport:
        if not self.repository.commit_exists(result_sha):
            raise EvidenceError(f"result SHA does not exist: {result_sha}")
        base_is_ancestor = self.repository.is_ancestor(base_sha, result_sha)
        if not base_is_ancestor:
            raise EvidenceError(f"result {result_sha} is not based on approved base {base_sha}")
        actual_head = self.repository.head(worktree)
        if actual_head != result_sha:
            raise EvidenceError(f"implementation worktree HEAD {actual_head} != result SHA {result_sha}")
        status = self.repository.status(worktree)
        untracked = tuple(line[3:] for line in status if line.startswith("?? "))
        changed_files_output = self.repository.run(
            "diff", "--name-only", f"{base_sha}..{result_sha}", cwd=worktree
        )
        changed = tuple(line for line in changed_files_output.splitlines() if line)
        prohibited = tuple(
            path
            for path in changed
            if self._matches(path, prohibited_paths) or not self._matches(path, allowed_paths)
        )
        governance = tuple(
            path for path in changed if path == "requirements.md" or path == "docs/DECISIONS.md"
        )
        if authorized_governance_changes:
            governance = ()

        deleted_tests = self.repository.run(
            "diff", "--name-only", "--diff-filter=D", f"{base_sha}..{result_sha}", cwd=worktree
        )
        suspicious_paths = {
            path
            for path in deleted_tests.splitlines()
            if "/tests/" in path or Path(path).name.startswith("test_")
        }
        numstat = self.repository.run("diff", "--numstat", f"{base_sha}..{result_sha}", cwd=worktree)
        for line in numstat.splitlines():
            added, deleted, path = line.split("\t", 2)
            if (
                ("/tests/" in path or Path(path).name.startswith("test_"))
                and deleted.isdigit()
                and int(deleted) > 0
            ):
                suspicious_paths.add(path)
        authorized_test_changes = authorized_test_changes or []
        suspicious = tuple(
            sorted(
                path
                for path in suspicious_paths
                if not self._matches(path, authorized_test_changes)
            )
        )
        commands = self._execute_test_commands(worktree, test_commands)
        report = EvidenceReport(
            base_sha=base_sha,
            result_sha=result_sha,
            branch=self.repository.branch(worktree),
            base_is_ancestor=base_is_ancestor,
            commits=tuple(
                line
                for line in self.repository.run(
                    "log", "--format=%H", f"{base_sha}..{result_sha}", cwd=worktree
                ).splitlines()
                if line
            ),
            changed_files=changed,
            untracked_files=untracked,
            worktree_clean=not status,
            prohibited_changes=prohibited,
            requirement_or_decision_changes=governance,
            suspicious_test_changes=suspicious,
            test_commands=commands,
        )
        failures = []
        if not report.worktree_clean:
            failures.append(f"worktree dirty: {status}")
        if prohibited:
            failures.append(f"prohibited/unauthorized paths changed: {list(prohibited)}")
        if governance:
            failures.append(f"requirements/decisions changed without authorization: {list(governance)}")
        if suspicious:
            failures.append(f"test files deleted or weakened: {list(suspicious)}")
        if any(exit_code != 0 for _, exit_code in commands):
            failures.append("one or more reported verification commands failed")
        if failures:
            raise EvidenceError("; ".join(failures))
        return report
