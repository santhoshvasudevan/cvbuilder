#!/usr/bin/env python3
"""Serialize commands that create or reuse the shared Django test database."""

from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 600.0
POLL_INTERVAL_SECONDS = 0.1
LOCK_FILE_NAME = "cvbuilder-test-database.lock"


def git_common_dir() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"cannot locate the shared Git directory: {detail}")
    path = Path(result.stdout.strip())
    return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()


def acquire_lock(lock_path: Path, timeout_seconds: float) -> int:
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BlockingIOError:
        print(
            f"Waiting for the shared test database lock at {lock_path} "
            f"(timeout {timeout_seconds:g}s)...",
            file=sys.stderr,
            flush=True,
        )

    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print("Acquired the shared test database lock; verification is starting.", flush=True)
            return descriptor
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(descriptor)
                raise TimeoutError(
                    f"timed out after {timeout_seconds:g}s waiting for the shared test database lock"
                )
            time.sleep(POLL_INTERVAL_SECONDS)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hold the repository-wide test-database lock while executing a command."
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    if not args.command:
        parser.error("a command is required after --")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        lock_path = git_common_dir() / LOCK_FILE_NAME
        descriptor = acquire_lock(lock_path, args.timeout)
    except (OSError, RuntimeError, TimeoutError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    os.set_inheritable(descriptor, True)
    try:
        os.execvp(args.command[0], args.command)
    except OSError as exc:
        print(f"ERROR: cannot execute {args.command[0]!r}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
