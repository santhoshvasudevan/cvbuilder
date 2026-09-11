"""Scripted adapter used by deterministic tests and dry-run exercises."""

from __future__ import annotations

from collections import deque
from typing import Callable, Iterable

from .base import AdapterRequest, AdapterResult, AgentAdapter


class FakeAdapter(AgentAdapter):
    def __init__(self, responses: Iterable[AdapterResult | dict]):
        self.responses = deque(responses)
        self.requests: list[AdapterRequest] = []
        self.terminated = False

    def start(
        self, request: AdapterRequest, event_callback: Callable[[str, dict], None] | None = None
    ) -> AdapterResult:
        self.requests.append(request)
        if not self.responses:
            return AdapterResult(status="FAILED", exit_code=1, error="fake response queue exhausted")
        value = self.responses.popleft()
        result = (
            value
            if isinstance(value, AdapterResult)
            else AdapterResult(
                status="COMPLETED",
                exit_code=0,
                session_id=value.get("session_id", "fake-session"),
                final_message="fake structured output",
                handoff=value,
            )
        )
        if event_callback:
            event_callback("stdout", {"type": "fake.result", "status": result.status})
        return result

    def terminate(self) -> None:
        self.terminated = True
