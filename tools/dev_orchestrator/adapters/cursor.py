"""Cursor CLI adapter for streamed non-interactive implementation sessions."""

from __future__ import annotations

from pathlib import Path

from .base import AdapterRequest, ProcessAdapter


class CursorAdapter(ProcessAdapter):
    def build_command(self, request: AdapterRequest, final_output_path: Path) -> list[str]:
        del final_output_path  # Cursor's final response is captured from stream-json.
        command = [
            self.binary,
            "--print",
            "--output-format",
            "stream-json",
            "--model",
            request.model,
            "--workspace",
            str(request.workdir),
            "--sandbox",
            "enabled",
        ]
        if request.allow_write:
            if request.permission_profile != "implementation_worktree" or not request.safety_verified:
                raise ValueError("Cursor write mode requires a verified isolated implementation worktree")
            command.extend(["--force"])
        else:
            command.extend(["--mode", "plan"])
        if request.session_id:
            command.extend(["--resume", request.session_id])
        # Cursor documents the prompt as a positional argument; passing it as one argv element is
        # safe (no shell is involved) and preserves embedded whitespace/newlines.
        command.append(request.prompt)
        return command

    def final_message_from_event(self, event: dict) -> str:
        event_type = str(event.get("type", "")).lower()
        if event_type in {"result", "final_result"}:
            value = event.get("result") or event.get("text") or event.get("message")
            return value if isinstance(value, str) else ""
        return super().final_message_from_event(event)
