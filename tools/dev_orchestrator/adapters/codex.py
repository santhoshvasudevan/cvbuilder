"""Codex CLI adapter based on flags exposed by ``codex exec --help``."""

from __future__ import annotations

from pathlib import Path

from .base import AdapterRequest, ProcessAdapter


class CodexAdapter(ProcessAdapter):
    def build_command(self, request: AdapterRequest, final_output_path: Path) -> list[str]:
        common = [
            "-m",
            request.model,
            "--json",
            "--output-last-message",
            str(final_output_path),
        ]
        if request.reasoning_effort:
            common.extend(["-c", f'model_reasoning_effort="{request.reasoning_effort}"'])
        if request.output_schema:
            common.extend(["--output-schema", str(request.output_schema)])
        if request.session_id:
            return [self.binary, "exec", "resume", *common, request.session_id, "-"]
        # Reviewers have an isolated audit worktree but still begin read-only. Workspace write is
        # enabled only by an explicit, safety-verified request (for operator-approved adversarial
        # tests), never merely because an audit worktree exists.
        sandbox = "workspace-write" if request.allow_write and request.safety_verified else "read-only"
        return [
            self.binary,
            "exec",
            "-C",
            str(request.workdir),
            "--sandbox",
            sandbox,
            *common,
            "-",
        ]

    def session_id_from_event(self, event: dict) -> str:
        if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
            return event["thread_id"]
        return super().session_id_from_event(event)
