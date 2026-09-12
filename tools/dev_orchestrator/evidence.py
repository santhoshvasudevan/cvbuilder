"""Deterministic candidate evidence collection and enforcement."""

from __future__ import annotations

import dataclasses
import fnmatch
import re
import shlex
import subprocess
from pathlib import Path
from typing import Callable

from .git_safety import GitRepository

# Bare revision endpoints for allowlisted `git diff --check` forms.
# Dots are allowed in names (e.g. tags) but never as range delimiters (`..`).
# Leading dashes (option injection) and shell/path metacharacters are rejected.
# Matching this pattern is not sufficient: endpoints must also resolve to commits.
_SAFE_GIT_REVISION_ENDPOINT = re.compile(r"\A(?:HEAD|[A-Za-z0-9][A-Za-z0-9._/@~^-]*)\Z")


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
    def _is_safe_git_revision_endpoint(value: str) -> bool:
        if not value or value.startswith("-") or ".." in value:
            return False
        return _SAFE_GIT_REVISION_ENDPOINT.fullmatch(value) is not None

    @classmethod
    def _parse_single_range_token(cls, value: str) -> tuple[str, str] | None:
        """Parse exactly one A..B or A...B token; both sides must be bare endpoints."""
        if "..." in value:
            left, sep, right = value.partition("...")
            if not sep or ".." in left or ".." in right:
                return None
            if cls._is_safe_git_revision_endpoint(left) and cls._is_safe_git_revision_endpoint(right):
                return left, right
            return None
        if ".." in value:
            left, sep, right = value.partition("..")
            if not sep or ".." in left or ".." in right:
                return None
            if cls._is_safe_git_revision_endpoint(left) and cls._is_safe_git_revision_endpoint(right):
                return left, right
            return None
        return None

    @classmethod
    def _git_diff_check_endpoints(cls, argv: list[str]) -> tuple[str, ...]:
        """
        Enforce the documented `git diff --check` grammar and return endpoints to verify.

        Accepted forms:
        - zero endpoints
        - one bare revision endpoint
        - two bare revision endpoints
        - exactly one A..B / A...B token whose sides contain no further range delimiters
        """
        if len(argv) < 3 or argv[1] != "diff" or argv[2] != "--check":
            raise EvidenceError(f"unapproved verification executable/arguments: {argv!r}")
        extras = argv[3:]
        if len(extras) > 2:
            raise EvidenceError(f"unapproved verification executable/arguments: {argv!r}")
        if len(extras) == 0:
            return ()
        if len(extras) == 2:
            # Two-token form: both must be bare endpoints (never range tokens).
            if not all(cls._is_safe_git_revision_endpoint(token) for token in extras):
                raise EvidenceError(f"unapproved verification executable/arguments: {argv!r}")
            return (extras[0], extras[1])
        token = extras[0]
        if ".." in token:
            parsed = cls._parse_single_range_token(token)
            if parsed is None:
                raise EvidenceError(f"unapproved verification executable/arguments: {argv!r}")
            return parsed
        if not cls._is_safe_git_revision_endpoint(token):
            raise EvidenceError(f"unapproved verification executable/arguments: {argv!r}")
        return (token,)

    @staticmethod
    def _revision_resolves_to_commit(endpoint: str, worktree: Path) -> bool:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{endpoint}^{{commit}}"],
            cwd=worktree,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def _verify_git_diff_check_endpoints(self, endpoints: tuple[str, ...], worktree: Path) -> None:
        for endpoint in endpoints:
            if not self._revision_resolves_to_commit(endpoint, worktree):
                raise EvidenceError(
                    f"git diff --check revision does not resolve to a commit: {endpoint!r}"
                )

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
        if executable == "git":
            if argv[1:] == ["status", "--short"]:
                return
            if len(argv) >= 3 and argv[1] == "diff" and argv[2] == "--check":
                EvidenceCollector._git_diff_check_endpoints(argv)
                return
            raise EvidenceError(f"unapproved verification executable/arguments: {argv!r}")
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

    def _prepare_verification_argv(self, argv: list[str], worktree: Path) -> list[str]:
        self._validate_verification_argv(argv)
        executable = Path(argv[0]).name
        if executable == "git" and len(argv) >= 3 and argv[1] == "diff" and argv[2] == "--check":
            endpoints = self._git_diff_check_endpoints(argv)
            self._verify_git_diff_check_endpoints(endpoints, worktree)
            # Disambiguate: force revision/range parsing; never treat tokens as pathspecs.
            if endpoints:
                return [*argv, "--"]
        return list(argv)

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
            exec_argv = self._prepare_verification_argv(argv, worktree)
            exit_code = self.command_runner(exec_argv, worktree)
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
