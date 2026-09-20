"""Claude CLI adapter using structured JSON output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .base import AdapterRequest, AdapterResult, ProcessAdapter
from .claude_schema import schema_argument_for_claude_cli


class ClaudeAdapter(ProcessAdapter):
    """Run Claude Code with the same subprocess contract as the other adapters."""

    READ_ONLY_TOOLS = ",".join(
        (
            "Read",
            "Glob",
            "Grep",
            "Bash(pwd)",
            "Bash(readlink *)",
            "Bash(shasum *)",
            "Bash(git branch --show-current)",
            "Bash(git rev-parse *)",
            "Bash(git status *)",
            "Bash(git log *)",
            "Bash(git diff *)",
            "Bash(make verify)",
            "Bash(make migrations-check)",
            "Bash(make secrets)",
            "Bash(.venv/bin/python manage.py test *)",
            "Bash(.venv/bin/python manage.py check *)",
            "Bash(.venv/bin/python manage.py makemigrations --check --dry-run)",
        )
    )
    WRITE_TOOLS = "Edit,Write,NotebookEdit"

    def __init__(self, binary: str, *, enabled: bool = False):
        super().__init__(binary)
        self.enabled = enabled

    def start(
        self, request: AdapterRequest, event_callback: Callable[[str, dict], None] | None = None
    ) -> AdapterResult:
        if not self.enabled:
            return AdapterResult(
                status="DISABLED",
                error="Claude adapter is disabled; no binary or authentication check was performed.",
            )
        return super().start(request, event_callback)

    @staticmethod
    def _schema_argument(path: Path) -> str:
        return schema_argument_for_claude_cli(path)

    def build_command(self, request: AdapterRequest, final_output_path: Path) -> list[str]:
        del final_output_path
        # Claude receives the prompt on stdin from ProcessAdapter. ``plan`` mode was rejected by a
        # live audit because it blocks even deterministic test execution. ``dontAsk`` permits
        # already-allowed read/test commands while denying anything that would require permission.
        # Accept edits only for the controller's separately safety-verified audit-test path.
        write_enabled = (
            request.allow_write
            and request.safety_verified
            and request.permission_profile == "audit_worktree"
        )
        command = [
            self.binary,
            "--print",
            "--output-format",
            "json",
            "--model",
            request.model,
            "--permission-mode",
            "acceptEdits" if write_enabled else "dontAsk",
            "--permission-prompts",
            "none",
        ]
        if request.reasoning_effort:
            command.extend(["--effort", request.reasoning_effort])
        if request.retry_reason == "reviewer_semantic_repair":
            # The controller already accepted the audit work. This bounded same-session retry may
            # only repair the final verdict/status relationship, never repeat tools or tests.
            command.extend(["--tools", ""])
        elif not write_enabled:
            command.extend(
                [
                    "--allowedTools",
                    self.READ_ONLY_TOOLS,
                    "--disallowedTools",
                    self.WRITE_TOOLS,
                ]
            )
        if request.output_schema:
            command.extend(["--json-schema", self._schema_argument(request.output_schema)])
        if request.session_id:
            command.extend(["--resume", request.session_id])
        return command

    def parse_event(self, line: str) -> dict:
        event = super().parse_event(line)
        usage = event.get("usage")
        if not isinstance(usage, dict):
            return event

        # Anthropic separates newly cached input from ordinary input and nests thinking output.
        # Normalize those provider fields without discarding the raw counters.
        normalized = dict(usage)
        normalized["input_tokens"] = self._integer(usage.get("input_tokens")) + self._integer(
            usage.get("cache_creation_input_tokens")
        )
        normalized["cached_input_tokens"] = self._integer(usage.get("cache_read_input_tokens"))
        details = usage.get("output_tokens_details")
        if isinstance(details, dict):
            normalized["reasoning_output_tokens"] = self._integer(details.get("thinking_tokens"))
        event["usage"] = normalized
        return event

    @staticmethod
    def _integer(value: object) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def final_message_from_event(self, event: dict) -> str:
        structured = event.get("structured_output")
        if isinstance(structured, dict):
            return json.dumps(structured, sort_keys=True)
        return super().final_message_from_event(event)
