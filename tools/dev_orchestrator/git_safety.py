"""Read-only Git boundary checks and non-destructive isolated-worktree creation."""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path


class GitSafetyError(RuntimeError):
    pass


ABANDONED_M3A_COMMITS = (
    "668b51e",
    "67646fd",
    "e6dee96",
    "1b9eb2e",
    "fc96059",
    "4c193c7",
    "02bbc5c",
)


@dataclasses.dataclass(frozen=True)
class GitBoundary:
    branch: str
    head: str
    clean: bool
    default_branch_is_ancestor: bool
    bootstrap_base_is_ancestor: bool
    excluded_ancestors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return (
            self.clean
            and self.default_branch_is_ancestor
            and self.bootstrap_base_is_ancestor
            and not self.excluded_ancestors
        )


class GitRepository:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def run(self, *args: str, cwd: Path | None = None, check: bool = True) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise GitSafetyError(f"git {' '.join(args)} failed: {detail}")
        return result.stdout.strip()

    def branch(self, cwd: Path | None = None) -> str:
        return self.run("branch", "--show-current", cwd=cwd)

    def head(self, cwd: Path | None = None) -> str:
        return self.run("rev-parse", "HEAD", cwd=cwd)

    def status(self, cwd: Path | None = None) -> list[str]:
        output = self.run("status", "--porcelain=v1", "--untracked-files=all", cwd=cwd)
        return output.splitlines() if output else []

    def commit_exists(self, sha: str) -> bool:
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        return result.returncode == 0

    def is_ancestor(self, ancestor: str, descendant: str = "HEAD") -> bool:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        return result.returncode == 0

    def verify_bootstrap_boundary(
        self,
        *,
        default_branch: str,
        bootstrap_branch: str,
        bootstrap_base_sha: str,
        require_clean: bool,
    ) -> GitBoundary:
        branch = self.branch()
        if branch not in {default_branch, bootstrap_branch}:
            raise GitSafetyError(
                f"unexpected branch {branch!r}; expected {bootstrap_branch!r} during bootstrap "
                f"or {default_branch!r} after operator fast-forward"
            )
        excluded = tuple(commit for commit in ABANDONED_M3A_COMMITS if self.is_ancestor(commit))
        boundary = GitBoundary(
            branch=branch,
            head=self.head(),
            clean=not self.status(),
            default_branch_is_ancestor=self.is_ancestor(default_branch),
            bootstrap_base_is_ancestor=self.is_ancestor(bootstrap_base_sha),
            excluded_ancestors=excluded,
        )
        if require_clean and not boundary.clean:
            raise GitSafetyError("working tree is not clean")
        if not boundary.default_branch_is_ancestor:
            raise GitSafetyError(f"{default_branch} is not an ancestor of HEAD")
        if not boundary.bootstrap_base_is_ancestor:
            raise GitSafetyError(f"bootstrap base {bootstrap_base_sha} is not an ancestor of HEAD")
        if excluded:
            raise GitSafetyError(f"abandoned M3A commits entered the branch: {list(excluded)}")
        return boundary

    def worktrees(self) -> list[dict[str, str]]:
        output = self.run("worktree", "list", "--porcelain")
        records: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in output.splitlines() + [""]:
            if not line:
                if current:
                    records.append(current)
                    current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value
        return records

    def _worktree_environment_sources(self) -> tuple[Path, Path]:
        virtualenv = self.root / ".venv"
        environment = self.root / ".env"
        if not virtualenv.is_dir():
            raise GitSafetyError(f"root virtualenv is missing: {virtualenv}")
        if not environment.is_file():
            raise GitSafetyError(f"root environment file is missing: {environment}")
        return virtualenv.resolve(), environment.resolve()

    def _bootstrap_worktree_environment(self, path: Path, sources: tuple[Path, Path]) -> None:
        virtualenv, environment = sources
        (path / ".venv").symlink_to(virtualenv, target_is_directory=True)
        (path / ".env").symlink_to(environment)

    def create_implementation_worktree(self, path: Path, branch: str, base_sha: str) -> None:
        registered = (Path(item["worktree"]).resolve() for item in self.worktrees())
        if path.exists() or path.resolve() in registered:
            raise GitSafetyError(f"implementation worktree path already exists: {path}")
        if not self.commit_exists(base_sha):
            raise GitSafetyError(f"implementation base SHA does not exist: {base_sha}")
        environment_sources = self._worktree_environment_sources()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.run("worktree", "add", "-b", branch, str(path), base_sha)
        self._bootstrap_worktree_environment(path, environment_sources)

    def create_audit_worktree(self, path: Path, candidate_sha: str) -> None:
        registered = (Path(item["worktree"]).resolve() for item in self.worktrees())
        if path.exists() or path.resolve() in registered:
            raise GitSafetyError(f"audit worktree path already exists: {path}")
        if not self.commit_exists(candidate_sha):
            raise GitSafetyError(f"audit candidate SHA does not exist: {candidate_sha}")
        environment_sources = self._worktree_environment_sources()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.run("worktree", "add", "--detach", str(path), candidate_sha)
        self._bootstrap_worktree_environment(path, environment_sources)

    def create_audit_branch(self, path: Path, branch: str, candidate_sha: str) -> None:
        if self.status(path):
            raise GitSafetyError("audit worktree must be clean before enabling test-only writes")
        if self.head(path) != candidate_sha:
            raise GitSafetyError("audit worktree moved from the candidate before test-only writes")
        self.run("switch", "-c", branch, candidate_sha, cwd=path)

    def validate_audit_test_commit(self, path: Path, candidate_sha: str, commit_sha: str) -> tuple[str, ...]:
        if not self.commit_exists(commit_sha) or not self.is_ancestor(candidate_sha, commit_sha):
            raise GitSafetyError("audit test commit is missing or not based on the candidate SHA")
        if self.head(path) != commit_sha or self.status(path):
            raise GitSafetyError("audit test worktree must be clean at the reported commit")
        changed = tuple(
            item
            for item in self.run(
                "diff", "--name-only", f"{candidate_sha}..{commit_sha}", cwd=path
            ).splitlines()
            if item
        )
        if not changed or any(
            not ("/tests/" in item or Path(item).name.startswith("test_")) for item in changed
        ):
            raise GitSafetyError("audit commit must contain test files only")
        return changed

    def assert_isolated_worktrees(self, implementation: Path, audit: Path) -> None:
        resolved = {self.root, implementation.resolve(), audit.resolve()}
        if len(resolved) != 3:
            raise GitSafetyError("root, implementation, and audit worktrees must be distinct")
        registered = {Path(item["worktree"]).resolve() for item in self.worktrees()}
        if implementation.resolve() not in registered or audit.resolve() not in registered:
            raise GitSafetyError("implementation and audit paths must both be registered worktrees")
